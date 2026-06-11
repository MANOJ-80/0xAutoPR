"""Code Reader Agent — fetch diff, clone repo, index into ChromaDB."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from git import Repo

from core.config import AppConfig, get_config
from core.github_client import GitHubClient
from core.language import detect_language
from core.state import ChangedFile, LogEntry, PipelineState
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _clone_repo(repo_url: str, dest: Path, branch: str, token: str) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    auth_url = repo_url
    if token and "github.com" in repo_url:
        auth_url = repo_url.replace("https://", f"https://x-access-token:{token}@")

    repo = Repo.clone_from(auth_url, dest, depth=1, branch=branch)
    return str(repo.working_dir)


def run_code_reader(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []

    full_name = state.get("repo_full_name", "")
    pr_number = state.get("pr_number", 0)
    head_branch = state.get("head_branch", "main")

    changed_files: list[ChangedFile] = []
    diff_raw = ""

    if cfg.has_github() and full_name and pr_number:
        gh = GitHubClient(cfg)
        diff_raw = gh.get_pr_diff(full_name, pr_number)
        for f in gh.get_pr_files(full_name, pr_number):
            changed_files.append(
                ChangedFile(
                    path=f.filename,
                    status=f.status,  # type: ignore[arg-type]
                    patch=f.patch,
                    content=f.content,
                    language=detect_language(f.filename),
                )
            )
        logs.append(LogEntry.create("code_reader", f"Fetched {len(changed_files)} changed files"))
    else:
        logs.append(
            LogEntry.create(
                "code_reader",
                "No GitHub credentials — using empty diff",
                level="warning",
            )
        )

    repo_slug = hashlib.md5(full_name.encode()).hexdigest()[:12]
    repo_path = Path(cfg.work_dir) / "repos" / repo_slug
    collection_name = f"repo_{repo_slug}"

    if cfg.has_github() and state.get("repo_url"):
        try:
            repo_path_str = _clone_repo(
                state["repo_url"].replace(".git", "") + ".git"
                if not state["repo_url"].endswith(".git")
                else state["repo_url"],
                repo_path,
                head_branch,
                cfg.github_token,
            )
            logs.append(LogEntry.create("code_reader", f"Cloned repo to {repo_path_str}"))
        except Exception as exc:
            repo_path_str = str(repo_path)
            repo_path.mkdir(parents=True, exist_ok=True)
            logs.append(
                LogEntry.create("code_reader", f"Clone failed: {exc}", level="warning")
            )
    else:
        repo_path_str = str(repo_path)
        repo_path.mkdir(parents=True, exist_ok=True)

    vs = VectorStore(cfg)
    repo_context = vs.index_repo(repo_path_str, collection_name)
    logs.append(
        LogEntry.create(
            "code_reader",
            f"Indexed {repo_context.chunk_count} chunks into vector store",
        )
    )

    return {
        "diff_raw": diff_raw,
        "changed_files": changed_files,
        "repo_context": repo_context,
        "repo_path": repo_path_str,
        "audit_log": logs,
    }
