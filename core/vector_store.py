"""ChromaDB vector store interface for repo context."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from core.config import AppConfig, get_config
from core.state import VectorStoreRef

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
        if end >= len(text):
            break
    return chunks


def _embed_texts(texts: list[str], config: AppConfig) -> list[list[float]]:
    if config.has_nvidia():
        return _embed_nim(texts, config)
    if config.has_gemini():
        return _embed_gemini(texts, config)
    return _embed_local(texts)

def _embed_nim(texts: list[str], config: AppConfig) -> list[list[float]]:
    from openai import OpenAI
    from core.llm import _nim_throttle

    _nim_throttle()  # Respect global NIM rate limit

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=config.nvidia_api_key,
        timeout=120.0,
        max_retries=3,
    )
    response = client.embeddings.create(
        input=texts,
        model=config.nim_models.embeddings,
        encoding_format="float",
    )
    return [data.embedding for data in response.data]


def _embed_gemini(texts: list[str], config: AppConfig) -> list[list[float]]:
    import google.generativeai as genai

    genai.configure(api_key=config.gemini_api_key)
    result = genai.embed_content(
        model=config.models.embedding_model,
        content=texts,
        task_type="retrieval_document",
    )
    embeddings = result.get("embedding", [])
    if embeddings and isinstance(embeddings[0], (int, float)):
        return [embeddings]
    return embeddings


def _embed_local(texts: list[str]) -> list[list[float]]:
    """Deterministic hash-based fallback when no embedding API is available."""
    vectors: list[list[float]] = []
    for text in texts:
        h = hashlib.sha256(text.encode()).digest()
        vectors.append([b / 255.0 for b in h[:64]] + [0.0] * (384 - min(64, len(h))))
    return vectors


class VectorStore:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        persist_dir = Path(self.config.chroma_persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

    def index_repo(
        self, repo_path: str, collection_name: str
    ) -> VectorStoreRef:
        """Index all source files in a repository."""
        try:
            self._client.delete_collection(collection_name)
        except Exception:
            pass

        collection = self._client.create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".chroma", "chroma",
        }
        extensions = {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java",
            ".rb", ".rs", ".md", ".json", ".yaml", ".yml",
        }

        repo = Path(repo_path)
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if Path(fname).suffix not in extensions:
                    continue
                fpath = Path(root) / fname
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    continue
                rel_path = str(fpath.relative_to(repo))
                for i, chunk in enumerate(_chunk_text(content)):
                    chunk_id = f"{rel_path}::{i}"
                    ids.append(chunk_id)
                    documents.append(chunk)
                    metadatas.append({"file": rel_path, "chunk": i})

        if not documents:
            return VectorStoreRef(
                collection_name=collection_name,
                chunk_count=0,
                persist_dir=self.config.chroma_persist_dir,
            )

        batch_size = 50
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i : i + batch_size]
            batch_ids = ids[i : i + batch_size]
            batch_meta = metadatas[i : i + batch_size]
            embeddings = _embed_texts(batch_docs, self.config)
            collection.add(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_meta,
                embeddings=embeddings,
            )

        logger.info("Indexed %d chunks from %s", len(documents), repo_path)
        return VectorStoreRef(
            collection_name=collection_name,
            chunk_count=len(documents),
            persist_dir=self.config.chroma_persist_dir,
        )

    def query(
        self, collection_name: str, query_text: str, n_results: int = 5
    ) -> list[dict]:
        if not collection_name:
            return []
        try:
            collection = self._client.get_collection(collection_name)
        except Exception:
            return []

        embeddings = _embed_texts([query_text], self.config)
        results = collection.query(
            query_embeddings=embeddings,
            n_results=min(n_results, collection.count() or 1),
        )
        output: list[dict] = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        for doc, meta in zip(docs, metas):
            output.append({"content": doc, "metadata": meta})
        return output
