"""Review Agent — parallel bug, security, and quality analysis passes."""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.config import AppConfig, get_config
from core.llm import generate_json_for_agent, is_llm_available
from core.state import Issue, LogEntry, PipelineState
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)

PASS_PROMPTS = {
    "bug": """You are a senior engineer performing a bug review on a pull request.

Analyze the following code changes for logic errors, null pointer risks, off-by-one errors,
race conditions, and incorrect assumptions.

Changed files:
{files}

Diff:
{diff}

Additional context from repository:
{context}

Return a JSON array of issues. Each issue must have:
- category: "bug"
- severity: one of "critical", "high", "medium", "low"
- file: file path
- line: line number (integer)
- title: short title
- explanation: detailed explanation
- confidence: float 0.0-1.0
- snippet: relevant code snippet

Return ONLY valid JSON array. If no issues found, return [].""",
    "security": """You are a security engineer reviewing a pull request.

Analyze for injection vulnerabilities, hardcoded secrets, improper auth, insecure dependencies,
and OWASP top-10 issues.

Changed files:
{files}

Diff:
{diff}

Additional context:
{context}

Return a JSON array of issues with fields: category ("security"), severity, file, line, title,
explanation, confidence (0.0-1.0), snippet.

Return ONLY valid JSON array. If no issues found, return [].""",
    "quality": """You are a code quality reviewer analyzing a pull request.

Look for code smells, missing error handling, performance anti-patterns, and maintainability issues.

Changed files:
{files}

Diff:
{diff}

Additional context:
{context}

Return a JSON array of issues with fields: category ("quality"), severity, file, line, title,
explanation, confidence (0.0-1.0), snippet.

Return ONLY valid JSON array. If no issues found, return [].""",
}



def _run_pass(
    category: str,
    state: PipelineState,
    context: str,
    config: AppConfig,
) -> list[Issue]:
    changed_files = state.get("changed_files", [])
    files_summary = "\n".join(f"- {cf.path} ({cf.language})" for cf in changed_files)
    diff = state.get("diff_raw", "")[:50000]
    prompt = PASS_PROMPTS[category].format(
        files=files_summary or "(none)",
        diff=diff or "(empty)",
        context=context[:10000],
    )

    if not is_llm_available():
        raise RuntimeError("LLM is completely unavailable and heuristics have been disabled.")

    try:
        result = generate_json_for_agent(prompt, agent="review_agent", config=config)
        if isinstance(result, list):
            raw_issues = result
        elif isinstance(result, dict) and "issues" in result:
            raw_issues = result["issues"]
    except Exception as exc:
        raise RuntimeError(f"LLM pass failed: {exc}")

    issues: list[Issue] = []
    for raw in raw_issues:
        try:
            issues.append(
                Issue(
                    id=str(uuid.uuid4())[:8],
                    category=raw.get("category", category),  # type: ignore[arg-type]
                    severity=raw.get("severity", "medium"),  # type: ignore[arg-type]
                    file=raw.get("file", ""),
                    line=int(raw.get("line", 1)),
                    title=raw.get("title", "Issue detected"),
                    explanation=raw.get("explanation", ""),
                    confidence=float(raw.get("confidence", 0.7)),
                    snippet=raw.get("snippet", ""),
                )
            )
        except (ValueError, TypeError) as exc:
            logger.debug("Skipping malformed issue: %s", exc)
    return issues


def _rank_issues(issues: list[Issue]) -> list[Issue]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(issues, key=lambda i: (order.get(i.severity, 4), -i.confidence))


def run_review_agent(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []

    context_parts: list[str] = []
    repo_context = state.get("repo_context")
    if repo_context and repo_context.collection_name:
        vs = VectorStore(cfg)
        query = state.get("diff_raw", "")[:2000] or "main code"
        for hit in vs.query(repo_context.collection_name, query, n_results=5):
            context_parts.append(hit["content"])

    context = "\n---\n".join(context_parts)
    all_issues: list[Issue] = []

    if is_llm_available():
        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = {
                pool.submit(_run_pass, cat, state, context, cfg): cat
                for cat in ("bug", "security", "quality")
            }
            for future in as_completed(futures):
                cat = futures[future]
                try:
                    found = future.result()
                    all_issues.extend(found)
                    logs.append(
                        LogEntry.create("review_agent", f"{cat} pass found {len(found)} issues")
                    )
                except Exception as exc:
                    logs.append(
                        LogEntry.create(
                            "review_agent", f"{cat} pass failed: {exc}", level="error"
                        )
                    )
    else:
        logs.append(LogEntry.create("review_agent", "LLM unavailable — pipeline cannot proceed without LLM.", level="error"))

    ranked = _rank_issues(all_issues)
    confidence = (
        sum(i.confidence for i in ranked) / len(ranked) if ranked else 1.0
    )

    return {
        "issues": ranked,
        "confidence_score": confidence,
        "audit_log": logs,
    }
