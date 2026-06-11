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
    msg = str(exc)
    match = re.search(r"try again in ([\d.]+)s", msg, re.I)
    if match:
        return float(match.group(1)) + 0.5
    return _BACKOFF_BASE ** attempt


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


def _call_nim(prompt: str, config: AppConfig, model: str) -> str:
    """Call a specific model on the NVIDIA NIM platform."""
    from openai import OpenAI

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=config.nvidia_api_key,
        timeout=60.0,
        max_retries=0,
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": sanitize_prompt(prompt)}],
        max_tokens=4096,
    )
    return completion.choices[0].message.content or ""


def generate_for_agent(
    prompt: str,
    *,
    agent: str,
    config: Optional[AppConfig] = None,
) -> str:
    """Generate text using the NIM model assigned to a specific agent.

    Each agent (review_agent, fix_writer, etc.) gets its own specialized
    model from the NIM platform.  Falls back to the generic provider chain
    if the NIM call fails.
    """
    cfg = config or get_config()

    # Resolve the NIM model for this agent
    nim_model = getattr(cfg.nim_models, agent, None)

    if nim_model and cfg.has_nvidia():
        try:
            logger.info("NIM call: agent=%s model=%s", agent, nim_model)
            return _retry(lambda p: _call_nim(p, cfg, nim_model), prompt)
        except Exception as exc:
            logger.warning(
                "NIM model %s failed for agent %s: %s — falling back",
                nim_model, agent, exc,
            )

    # Fallback to the generic multi-provider chain
    return generate(prompt, config=cfg)


def generate(
    prompt: str,
    *,
    prefer: str = "gemini",
    json_mode: bool = False,
    config: Optional[AppConfig] = None,
) -> str:
    """Generate text using the best available provider."""
    if not is_llm_available():
        raise RuntimeError(
            _llm_disable_reason or "LLM disabled (HEURISTICS_ONLY or rate limit)"
        )

    cfg = config or get_config()
    providers: list[tuple[str, callable]] = []

    # NIM platform as highest priority — use the fix_writer model as default
    if cfg.has_nvidia():
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
