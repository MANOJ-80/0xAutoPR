"""LLM provider abstraction with free-tier fallbacks and rate-limit circuit breaker."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional

from core.config import AppConfig, get_config

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_DAILY_LIMIT_WAIT_THRESHOLD = 60.0  # seconds — trip breaker instead of waiting

_llm_disabled = False
_llm_disable_reason = ""
_llm_lock = threading.Lock()

# ── Global NIM rate limiter (token bucket) ──────────────────────────────
# NVIDIA free tier = 40 RPM.  We pace to 30 RPM (2s between calls) to
# leave headroom for bursts and avoid ever hitting the wall.
_NIM_MIN_INTERVAL = 2.0          # seconds between consecutive NIM calls
_nim_last_call_time = 0.0        # epoch timestamp of last NIM request
_nim_rate_lock = threading.Lock()


def is_llm_available() -> bool:
    """Return False when rate-limited or HEURISTICS_ONLY is set."""
    if _llm_disabled:
        return False
    cfg = get_config()
    if cfg.heuristics_only:
        return False
    return cfg.has_nvidia() or cfg.has_gemini() or cfg.has_groq() or bool(cfg.openrouter_api_key)


def disable_llm(reason: str) -> None:
    global _llm_disabled, _llm_disable_reason
    with _llm_lock:
        if not _llm_disabled:
            _llm_disabled = True
            _llm_disable_reason = reason
            logger.warning(
                "LLM circuit breaker tripped: %s — using heuristics for rest of run",
                reason,
            )


def reset_llm_circuit() -> None:
    global _llm_disabled, _llm_disable_reason
    with _llm_lock:
        _llm_disabled = False
        _llm_disable_reason = ""
    cfg = get_config()
    if cfg.heuristics_only:
        disable_llm("HEURISTICS_ONLY=true")


def sanitize_prompt(text: str) -> str:
    """Strip patterns that could be used for prompt injection from code."""
    patterns = [
        r"(?i)ignore\s+(all\s+)?previous\s+instructions",
        r"(?i)you\s+are\s+now\s+",
        r"(?i)system\s*:\s*",
        r"(?i)assistant\s*:\s*",
        r"<\|im_start\|>",
        r"<\|im_end\|>",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "[FILTERED]", result)
    return result


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def _is_daily_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "tokens per day" in msg or "tpd" in msg


def _rate_limit_wait(exc: Exception, attempt: int) -> float:
    """For 429s, wait at least 30s on first hit and scale up.
    NVIDIA's rate-limit window is 60s, so short waits just burn retries."""
    msg = str(exc)
    match = re.search(r"try again in ([\d.]+)s", msg, re.I)
    if match:
        return float(match.group(1)) + 1.0
    # 30s, 45s, 60s, 60s, 60s — actually survive the 60s window
    return min(30.0 * (1.5 ** attempt), 60.0)


def _retry(fn, *args, **kwargs) -> str:
    last_err: Optional[Exception] = None
    max_retries = 5  # Increased to 5 to handle NVIDIA rate limits
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_err = exc
            if _is_daily_limit(exc):
                raise RuntimeError(f"Daily token limit exceeded: {exc}") from exc
            
            wait_time = _rate_limit_wait(exc, attempt) if _is_rate_limit(exc) else min(_BACKOFF_BASE ** attempt, 10.0)
            logger.warning("LLM call failed (attempt %d/%d). Waiting %.1fs... %s", attempt + 1, max_retries, wait_time, exc)
            time.sleep(wait_time)
            
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_err}")


def _call_gemini(prompt: str, config: AppConfig, json_mode: bool = False) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.gemini_api_key)
    model = genai.GenerativeModel(config.models.gemini_model)
    generation_config: dict[str, Any] = {}
    if json_mode:
        generation_config["response_mime_type"] = "application/json"
    response = model.generate_content(
        sanitize_prompt(prompt),
        generation_config=generation_config or None,
    )
    return response.text or ""


def _call_groq(prompt: str, config: AppConfig) -> str:
    from groq import Groq

    client = Groq(api_key=config.groq_api_key)
    response = client.chat.completions.create(
        model=config.models.groq_model,
        messages=[{"role": "user", "content": sanitize_prompt(prompt)}],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def _call_openrouter(prompt: str, config: AppConfig) -> str:
    import httpx

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.openrouter_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.models.openrouter_model,
            "messages": [{"role": "user", "content": sanitize_prompt(prompt)}],
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_ollama(prompt: str, config: AppConfig) -> str:
    import httpx

    response = httpx.post(
        f"{config.ollama_base_url}/api/generate",
        json={"model": config.models.ollama_model, "prompt": sanitize_prompt(prompt), "stream": False},
        timeout=600.0,
    )
    response.raise_for_status()
    return response.json().get("response", "")


def _call_cerebras(prompt: str, config: AppConfig) -> str:
    import httpx

    response = httpx.post(
        "https://api.cerebras.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.cerebras_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.models.cerebras_model,
            "messages": [{"role": "user", "content": sanitize_prompt(prompt)}],
            "temperature": 0.2,
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _nim_throttle() -> None:
    """Block until enough time has passed since the last NIM call.
    This enforces a global 30 RPM ceiling (2s spacing) across all threads."""
    global _nim_last_call_time
    with _nim_rate_lock:
        now = time.monotonic()
        elapsed = now - _nim_last_call_time
        if elapsed < _NIM_MIN_INTERVAL:
            wait = _NIM_MIN_INTERVAL - elapsed
            logger.debug("NIM throttle: waiting %.1fs", wait)
            time.sleep(wait)
        _nim_last_call_time = time.monotonic()


def _call_nim(prompt: str, config: AppConfig, model: str) -> str:
    """Call a specific model on the NVIDIA NIM platform."""
    from openai import OpenAI

    _nim_throttle()  # Enforce global rate limit BEFORE the request

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=config.nvidia_api_key,
        timeout=120.0,
        max_retries=0,
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": sanitize_prompt(prompt)}],
        max_tokens=4096,
    )
    return completion.choices[0].message.content or ""


def _get_cache_db() -> Path:
    import sqlite3
    cfg = get_config()
    db_dir = Path(cfg.chroma_persist_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "llm_cache.db"
    
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS llm_cache (
                hash TEXT PRIMARY KEY,
                response TEXT
            )
        ''')
    return db_path

def _get_cached_response(prompt: str, model: str) -> Optional[str]:
    import hashlib
    import sqlite3
    db_path = _get_cache_db()
    h = hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        cursor = conn.execute("SELECT response FROM llm_cache WHERE hash = ?", (h,))
        row = cursor.fetchone()
        return row[0] if row else None

def _set_cached_response(prompt: str, model: str, response: str) -> None:
    import hashlib
    import sqlite3
    db_path = _get_cache_db()
    h = hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.execute("INSERT OR REPLACE INTO llm_cache (hash, response) VALUES (?, ?)", (h, response))

def generate_for_agent(
    prompt: str,
    *,
    agent: str,
    config: Optional[AppConfig] = None,
) -> str:
    """Generate text using the NIM model assigned to a specific agent.

    NIM is the sole provider.  On rate limits (429), we wait out the full
    60-second window and retry instead of falling back to nothing.
    Uses SQLite caching to instantly return identical queries.
    """
    cfg = config or get_config()
    nim_model = getattr(cfg.nim_models, agent, None)

    if not (nim_model and cfg.has_nvidia()):
        raise RuntimeError(f"No NIM model configured for agent '{agent}'")

    cached = _get_cached_response(prompt, nim_model)
    if cached is not None:
        logger.info("NIM call: agent=%s model=%s (CACHED)", agent, nim_model)
        return cached

    last_err: Optional[Exception] = None
    max_attempts = 8  # Enough attempts to survive multiple 60s rate-limit windows

    for attempt in range(max_attempts):
        try:
            _nim_throttle()
            logger.info("NIM call: agent=%s model=%s (attempt %d/%d)", agent, nim_model, attempt + 1, max_attempts)

            from openai import OpenAI
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=cfg.nvidia_api_key,
                timeout=120.0,
                max_retries=0,
            )
            completion = client.chat.completions.create(
                model=nim_model,
                messages=[{"role": "user", "content": sanitize_prompt(prompt)}],
                max_tokens=4096,
            )
            response_text = completion.choices[0].message.content or ""
            _set_cached_response(prompt, nim_model, response_text)
            return response_text

        except Exception as exc:
            last_err = exc
            if _is_daily_limit(exc):
                raise RuntimeError(f"Daily token limit exceeded: {exc}") from exc

            if _is_rate_limit(exc) or "429" in str(exc):
                # Wait out the full rate-limit window (60s) + jitter
                wait = 65.0
                logger.warning(
                    "NIM rate-limited (attempt %d/%d). Cooling down %.0fs before retry... %s",
                    attempt + 1, max_attempts, wait, exc,
                )
                time.sleep(wait)
            elif "timed out" in str(exc).lower():
                # Timeout — short wait, the server was just slow
                wait = 5.0
                logger.warning(
                    "NIM timeout (attempt %d/%d). Waiting %.0fs... %s",
                    attempt + 1, max_attempts, wait, exc,
                )
                time.sleep(wait)
            else:
                # Other error — exponential backoff
                wait = min(_BACKOFF_BASE ** attempt, 30.0)
                logger.warning(
                    "NIM error (attempt %d/%d). Waiting %.0fs... %s",
                    attempt + 1, max_attempts, wait, exc,
                )
                time.sleep(wait)

    raise RuntimeError(f"NIM call failed for agent '{agent}' after {max_attempts} attempts: {last_err}")


def generate(
    prompt: str,
    *,
    prefer: str = "gemini",
    json_mode: bool = False,
    config: Optional[AppConfig] = None,
    _skip_nim: bool = False,
) -> str:
    """Generate text using the best available provider."""
    if not is_llm_available():
        raise RuntimeError(
            _llm_disable_reason or "LLM disabled (HEURISTICS_ONLY or rate limit)"
        )

    cfg = config or get_config()
    providers: list[tuple[str, callable]] = []

    # NIM platform as highest priority — use the fix_writer model as default
    if cfg.has_nvidia() and not _skip_nim:
        default_nim = cfg.nim_models.fix_writer
        providers.append(("nvidia/nim", lambda p: _call_nim(p, cfg, default_nim)))

    # Inject cerebras as next priority if available
    if cfg.has_cerebras():
        providers.append(("cerebras", lambda p: _call_cerebras(p, cfg)))

    if prefer == "groq" and cfg.has_groq():
        providers.append(("groq", lambda p: _call_groq(p, cfg)))
    if prefer == "openrouter" and cfg.openrouter_api_key:
        providers.append(("openrouter", lambda p: _call_openrouter(p, cfg)))
    if cfg.has_gemini():
        providers.append(("gemini", lambda p: _call_gemini(p, cfg, json_mode)))
    if cfg.has_groq() and prefer != "groq":
        providers.append(("groq", lambda p: _call_groq(p, cfg)))
    if cfg.openrouter_api_key and prefer != "openrouter":
        providers.append(("openrouter", lambda p: _call_openrouter(p, cfg)))
    # Always add ollama as the absolute last resort fallback
    providers.append(("ollama", lambda p: _call_ollama(p, cfg)))

    errors: list[str] = []
    for name, fn in providers:
        if not is_llm_available():
            break
        try:
            return _retry(fn, prompt)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            if _is_rate_limit(exc):
                logger.warning("Provider %s rate-limited: %s", name, exc)
            else:
                logger.warning("Provider %s unavailable: %s", name, exc)

    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    
    raise ValueError(f"Failed to extract JSON from LLM response: {text[:100]}...")


def generate_json(prompt: str, **kwargs: Any) -> Any:
    """Generate and parse JSON from an LLM response."""
    prefer = kwargs.get("prefer", "gemini")
    cfg = kwargs.get("config") or get_config()
    json_mode = prefer == "gemini" and cfg.has_gemini()
    text = generate(prompt, json_mode=json_mode, **kwargs)
    return _extract_json(text)


def generate_json_for_agent(prompt: str, *, agent: str, config: Optional[AppConfig] = None) -> Any:
    """Generate and parse JSON using the NIM model assigned to a specific agent."""
    text = generate_for_agent(prompt, agent=agent, config=config)
    return _extract_json(text)
