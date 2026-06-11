"""Free-tier model and application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class NIMModelConfig:
    """Per-agent specialized NIM model assignments."""
    orchestrator: str = "moonshotai/kimi-k2.6"
    code_reader: str = "qwen/qwen3.5-397b-a17b"
    review_agent: str = "meta/llama-3.1-70b-instruct"
    fix_writer: str = "mistralai/mistral-large-3-675b-instruct-2512"
    patch_generator: str = "mistralai/mistral-nemotron"
    test_writer: str = "nvidia/nemotron-3-super-120b-a12b"
    pr_opener: str = "stepfun-ai/step-3.7-flash"
    embeddings: str = "nvidia/nv-embedcode-7b-v1"


@dataclass
class ModelConfig:
    """Legacy model config for non-NIM fallback providers."""
    gemini_model: str = "gemini-2.0-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    embedding_model: str = "models/gemini-embedding-2"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    ollama_model: str = "qwen2.5:7b-instruct"
    cerebras_model: str = "gpt-oss-120b"


@dataclass
class ThresholdConfig:
    fix_min_confidence: float = 0.6
    escalate_confidence: float = 0.75
    max_retries: int = 3
    min_severity_for_fix: str = "medium"


@dataclass
class AppConfig:
    github_token: str = ""
    github_webhook_secret: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    cerebras_api_key: str = ""
    nvidia_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    chroma_persist_dir: str = ""
    work_dir: str = ""
    port: int = 8080
    host: str = "0.0.0.0"
    log_level: str = "INFO"
    dry_run: bool = False
    heuristics_only: bool = False
    models: ModelConfig = field(default_factory=ModelConfig)
    nim_models: NIMModelConfig = field(default_factory=NIMModelConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)

    @classmethod
    def _resolve_github_token(cls) -> str:
        token = os.getenv("GITHUB_TOKEN", "")
        if token:
            return token
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        base = Path(os.getenv("AUTOXPR_WORK_DIR", "/tmp/0xautopr"))
        return cls(
            github_token=cls._resolve_github_token(),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            cerebras_api_key=os.getenv("CEREBRAS_API_KEY", ""),
            nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            chroma_persist_dir=os.getenv(
                "CHROMA_PERSIST_DIR", str(base / "chroma")
            ),
            work_dir=os.getenv("AUTOXPR_WORK_DIR", str(base)),
            port=int(os.getenv("PORT", "8080")),
            host=os.getenv("HOST", "0.0.0.0"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            dry_run=os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes"),
            heuristics_only=os.getenv("HEURISTICS_ONLY", "false").lower()
            in ("1", "true", "yes"),
        )

    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    def has_github(self) -> bool:
        return bool(self.github_token)

    def has_cerebras(self) -> bool:
        return bool(self.cerebras_api_key)

    def has_nvidia(self) -> bool:
        return bool(self.nvidia_api_key)


def get_config() -> AppConfig:
    return AppConfig.from_env()


def effective_escalate_threshold(config: AppConfig | None = None) -> float:
    """Lower bar when using heuristics — rule-based fixes are deterministic."""
    cfg = config or get_config()
    if cfg.heuristics_only:
        return 0.5
    from core.llm import is_llm_available

    if not is_llm_available():
        return 0.5
    return cfg.thresholds.escalate_confidence
