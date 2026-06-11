"""Orchestrator Agent — LangGraph state machine coordinating all agents."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from pathlib import Path

from agents.code_reader import run_code_reader
from agents.fix_writer import run_fix_writer
from agents.patch_generator import run_patch_generator
from agents.pr_opener import run_pr_opener
from agents.review_agent import run_review_agent
from agents.test_writer import run_test_writer
from core.config import AppConfig, effective_escalate_threshold, get_config
from core.llm import reset_llm_circuit
from core.github_client import GitHubClient
from core.state import LogEntry, PipelineState, initial_state

logger = logging.getLogger(__name__)


def _log(state: PipelineState, agent: str, message: str, **data: Any) -> list[LogEntry]:
    return [LogEntry.create(agent, message, **data)]


def node_code_reader(state: PipelineState) -> dict:
    logger.info("=> [1/8] Executing code_reader...")
    result = run_code_reader(state)
    return result


def node_review(state: PipelineState) -> dict:
    logger.info("=> [2/8] Executing review_agent...")
    return run_review_agent(state)


def node_fix_writer(state: PipelineState) -> dict:
    logger.info("=> [3/8] Executing fix_writer...")
    issues = state.get("issues", [])
    cfg = get_config()

    actionable = [
        i for i in issues if i.confidence >= cfg.thresholds.fix_min_confidence
    ]
    if not actionable:
        comment_only = [
            i for i in issues if i.confidence < cfg.thresholds.fix_min_confidence
        ]
        if comment_only and cfg.has_github():
            try:
                gh = GitHubClient(cfg)
                body = "## 0xAutoPR Review (comment-only)\n\n"
                body += "Issues below confidence threshold for auto-fix:\n\n"
                for i in comment_only:
                    body += f"- **[{i.severity}]** `{i.file}:{i.line}` — {i.title}\n"
                gh.post_comment(
                    state.get("repo_full_name", ""),
                    state.get("pr_number", 0),
                    body,
                )
            except Exception as exc:
                logger.warning("Failed to post comment: %s", exc)

        return {
            "status": "skipped",
            "audit_log": _log(state, "orchestrator", "No actionable issues — skipped"),
        }

    return run_fix_writer(state)


def node_patch_generator(state: PipelineState) -> dict:
    logger.info("=> [4/8] Executing patch_generator...")
    if state.get("status") == "skipped":
        return {}
    return run_patch_generator(state)


def node_test_writer(state: PipelineState) -> dict:
    logger.info("=> [5/8] Executing test_writer...")
    if state.get("status") == "skipped":
        return {}
    return run_test_writer(state)


def _restore_repo(state: PipelineState) -> None:
    repo_path = state.get("repo_path", "")
    if not repo_path:
        return
    for cf in state.get("changed_files", []):
        fpath = Path(repo_path) / cf.path
        if cf.content and fpath.parent.exists():
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(cf.content, encoding="utf-8")


def node_validate(state: PipelineState) -> dict:
    logger.info("=> [6/8] Executing validate...")
    cfg = get_config()
    logs: list[LogEntry] = []

    if state.get("status") == "skipped":
        return {"audit_log": logs}

    fixes = state.get("fixes", [])
    patches = state.get("patches", [])
    test_results = state.get("test_results", [])

    if not fixes or not patches:
        logs.append(LogEntry.create("orchestrator", "No fixes produced — skipping"))
        return {"status": "skipped", "audit_log": logs}

    if not test_results:
        all_passed = False
    else:
        all_passed = all(tr.passed for tr in test_results)
    confidence = state.get("confidence_score", 0.0)
    retry_count = state.get("retry_count", 0)

    if not all_passed:
        if retry_count < cfg.thresholds.max_retries:
            logs.append(
                LogEntry.create(
                    "orchestrator",
                    f"Tests failed — retry {retry_count + 1}/{cfg.thresholds.max_retries}",
                    level="warning",
                )
            )
            _restore_repo(state)
            return {
                "retry_count": retry_count + 1,
                "fixes": [],
                "patches": [],
                "test_results": [],
                "status": "running",
                "audit_log": logs,
            }
        logs.append(
            LogEntry.create(
                "orchestrator",
                "Tests failed after max retries — escalating",
                level="error",
            )
        )
        return {"status": "escalated", "audit_log": logs}

    threshold = effective_escalate_threshold(cfg)
    if confidence < threshold:
        logs.append(
            LogEntry.create(
                "orchestrator",
                f"Confidence {confidence:.2f} below threshold {threshold:.2f} — escalating",
                level="warning",
            )
        )
        if cfg.has_github():
            try:
                gh = GitHubClient(cfg)
                gh.post_comment(
                    state.get("repo_full_name", ""),
                    state.get("pr_number", 0),
                    f"## 0xAutoPR — Human Review Required\n\n"
                    f"Confidence score **{confidence:.2%}** is below the "
                    f"auto-fix threshold ({threshold:.0%}). "
                    f"Please review findings manually.",
                )
            except Exception:
                pass
        return {"status": "escalated", "audit_log": logs}

    logs.append(LogEntry.create("orchestrator", "Validation passed — proceeding to PR"))
    return {"audit_log": logs}


def _route_after_validate(state: PipelineState) -> Literal["retry", "pr_opener", "end"]:
    status = state.get("status", "running")
    if status in ("escalated", "skipped", "failed"):
        return "end"

    fixes = state.get("fixes", [])
    patches = state.get("patches", [])
    retry_count = state.get("retry_count", 0)
    cfg = get_config()

    if not fixes or not patches:
        if retry_count > 0 and retry_count <= cfg.thresholds.max_retries:
            return "retry"
        return "end"

    confidence = state.get("confidence_score", 0.0)
    if confidence < effective_escalate_threshold(cfg):
        return "end"

    return "pr_opener"


def node_pr_opener(state: PipelineState) -> dict:
    logger.info("=> [8/8] Executing pr_opener...")
    return run_pr_opener(state)


def _route_after_re_review(state: PipelineState) -> Literal["pr_opener", "end"]:
    if state.get("status") == "escalated":
        return "end"
    if not state.get("fixes") or not state.get("patches"):
        return "end"
    return "pr_opener"


def _finalize_state(state: PipelineState) -> PipelineState:
    status = state.get("status", "running")
    if status != "running":
        return state
    if state.get("output_pr_url"):
        state["status"] = "fixed"
    elif state.get("fixes") and state.get("patches"):
        state["status"] = "escalated"
    elif state.get("issues"):
        state["status"] = "skipped"
    else:
        state["status"] = "failed"
    return state


def node_re_review(state: PipelineState) -> dict:
    logger.info("=> [7/8] Executing re_review...")
    """Ensure fixes did not introduce new critical vulnerabilities."""
    import re
    dangerous = (
        (r"(?<!literal_)eval\(", "eval() introduced by fix"),
        (r"(?<!_)exec\(", "exec() introduced by fix"),
        (r"pickle\.loads\(", "unsafe deserialization introduced by fix"),
        (r"__import__\(['\"]os['\"]\)\.system\(", "shell execution introduced by fix"),
    )
    for fix in state.get("fixes", []):
        replacement = "\n".join(fix.replacement_lines)
        for pattern, reason in dangerous:
            if re.search(pattern, replacement):
                return {
                    "status": "escalated",
                    "audit_log": _log(
                        state,
                        "orchestrator",
                        f"Re-review blocked fix in {fix.file}: {reason}",
                        level="warning",
                    ),
                }
    return {"audit_log": _log(state, "orchestrator", "Re-review passed — no regressions in fixes")}


def build_pipeline() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("code_reader", node_code_reader)
    graph.add_node("review", node_review)
    graph.add_node("fix_writer", node_fix_writer)
    graph.add_node("patch_generator", node_patch_generator)
    graph.add_node("test_writer", node_test_writer)
    graph.add_node("validate", node_validate)
    graph.add_node("re_review", node_re_review)
    graph.add_node("pr_opener", node_pr_opener)

    graph.set_entry_point("code_reader")
    graph.add_edge("code_reader", "review")
    graph.add_edge("review", "fix_writer")
    graph.add_edge("fix_writer", "patch_generator")
    graph.add_edge("patch_generator", "test_writer")
    graph.add_edge("test_writer", "validate")
    graph.add_conditional_edges(
        "validate",
        _route_after_validate,
        {
            "retry": "fix_writer",
            "pr_opener": "re_review",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "re_review",
        _route_after_re_review,
        {"pr_opener": "pr_opener", "end": END},
    )
    graph.add_edge("pr_opener", END)

    return graph


def run_pipeline(
    *,
    pr_url: str,
    pr_number: int,
    repo_url: str,
    repo_full_name: str,
    base_branch: str,
    head_branch: str,
    config: AppConfig | None = None,
) -> PipelineState:
    """Execute the full 0xAutoPR pipeline."""
    state = initial_state(
        pr_url=pr_url,
        pr_number=pr_number,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        base_branch=base_branch,
        head_branch=head_branch,
    )

    graph = build_pipeline()
    app = graph.compile()

    reset_llm_circuit()
    cfg = config or get_config()
    logger.info("Starting pipeline for PR #%d in %s", pr_number, repo_full_name)
    if cfg.heuristics_only:
        logger.info("HEURISTICS_ONLY mode — no LLM calls will be made")
    final_state: PipelineState = _finalize_state(app.invoke(state))
    logger.info("Pipeline finished with status: %s", final_state.get("status"))
    return final_state
