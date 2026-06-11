"""Fix Writer Agent — generate code fixes for detected issues."""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import AppConfig, get_config
from core.llm import generate_json_for_agent, is_llm_available
from core.state import Fix, Issue, LogEntry, PipelineState

logger = logging.getLogger(__name__)

FIX_PROMPT = """You are a precise code fixer. Fix ONLY the specific issue described.
Do NOT refactor unrelated code or change style.

Issue:
- File: {file}
- Line: {line}
- Severity: {severity}
- Title: {title}
- Explanation: {explanation}
- Snippet: {snippet}

Full file content:
```
{content}
```

Return JSON with:
- original_lines: list of lines to replace (exact text)
- replacement_lines: list of replacement lines
- start_line: int (1-based)
- end_line: int (1-based, inclusive)
- explanation: brief fix explanation
- confidence: float 0.0-1.0

Return ONLY valid JSON object."""



def _generate_fix(issue: Issue, file_content: str, config: AppConfig) -> Fix | None:
    if issue.confidence < config.thresholds.fix_min_confidence:
        return None

    if not is_llm_available():
        raise RuntimeError("LLM is unavailable, cannot generate fixes.")

    prompt = FIX_PROMPT.format(
        file=issue.file,
        line=issue.line,
        severity=issue.severity,
        title=issue.title,
        explanation=issue.explanation,
        snippet=issue.snippet,
        content=file_content[:30000],
    )

    try:
        result = generate_json_for_agent(prompt, agent="fix_writer", config=config)
        return Fix(
            issue_id=issue.id,
            file=issue.file,
            original_lines=result.get("original_lines", []),
            replacement_lines=result.get("replacement_lines", []),
            start_line=int(result.get("start_line", issue.line)),
            end_line=int(result.get("end_line", issue.line)),
            explanation=result.get("explanation", issue.title),
            confidence=float(result.get("confidence", issue.confidence)),
        )
    except Exception as exc:
        logger.warning("LLM fix failed for %s: %s", issue.id, exc)
        return None


def run_fix_writer(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []
    issues: list[Issue] = state.get("issues", [])
    changed_files = {cf.path: cf.content for cf in state.get("changed_files", [])}
    repo_path = state.get("repo_path", "")

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    min_sev = severity_order.get(cfg.thresholds.min_severity_for_fix, 2)

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    eligible = sorted(
        issues,
        key=lambda i: (severity_rank.get(i.severity, 4), -i.confidence),
    )[:3]  # Cap at 3 to stay within NVIDIA NIM free-tier rate limits

    fixes: list[Fix] = []
    for issue in eligible:
        if severity_order.get(issue.severity, 4) > min_sev:
            continue
        if issue.confidence < cfg.thresholds.fix_min_confidence:
            logs.append(
                LogEntry.create(
                    "fix_writer",
                    f"Skipped issue {issue.id} — confidence {issue.confidence:.2f} below threshold",
                    level="info",
                )
            )
            continue

        content = changed_files.get(issue.file, "")
        if not content and repo_path:
            fpath = Path(repo_path) / issue.file
            if fpath.exists():
                content = fpath.read_text(encoding="utf-8", errors="replace")

        if not content:
            logs.append(
                LogEntry.create(
                    "fix_writer",
                    f"No content for {issue.file}",
                    level="warning",
                )
            )
            continue

        fix = _generate_fix(issue, content, cfg)
        if fix and fix.replacement_lines:
            fixes.append(fix)
            logs.append(LogEntry.create("fix_writer", f"Generated fix for issue {issue.id}"))

    avg_conf = sum(f.confidence for f in fixes) / len(fixes) if fixes else state.get("confidence_score", 0.0)

    return {
        "fixes": fixes,
        "confidence_score": avg_conf,
        "audit_log": logs,
    }
