"""PR Opener Agent — push fix branch and open corrective PR."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from core.config import AppConfig, get_config
from core.github_client import GitHubClient, build_pr_description
from core.state import LogEntry, PipelineState

logger = logging.getLogger(__name__)


def run_pr_opener(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []

    fixes = state.get("fixes", [])
    patches = state.get("patches", [])
    pr_number = state.get("pr_number", 0)
    full_name = state.get("repo_full_name", "")
    base_branch = state.get("base_branch", "main")
    original_pr_url = state.get("pr_url", "")

    if not fixes or not patches:
        logs.append(LogEntry.create("pr_opener", "No fixes to publish — skipping PR"))
        return {
            "status": "skipped",
            "audit_log": logs,
        }

    short_hash = hashlib.md5(
        "".join(p.unified_diff for p in patches).encode()
    ).hexdigest()[:8]
    branch_name = f"0xautoPR/fix-{pr_number}-{short_hash}"

    description = build_pr_description(
        issues=state.get("issues", []),
        fixes=fixes,
        test_results=state.get("test_results", []),
        confidence_score=state.get("confidence_score", 0.0),
        original_pr_url=original_pr_url,
    )

    title = f"fix: 0xAutoPR automated fixes for PR #{pr_number}"

    files_to_push: dict[str, str] = {}
    repo_path = state.get("repo_path", "")
    for patch in patches:
        if repo_path:
            fpath = Path(repo_path) / patch.file
            if fpath.exists():
                files_to_push[patch.file] = fpath.read_text(encoding="utf-8")

    for tr in state.get("test_results", []):
        if repo_path:
            test_fpath = Path(repo_path) / tr.test_file
            if test_fpath.exists():
                files_to_push[tr.test_file] = test_fpath.read_text(encoding="utf-8")

    commit_message = patches[0].commit_message if patches else title

    if cfg.dry_run or not cfg.has_github():
        logs.append(
            LogEntry.create(
                "pr_opener",
                f"Dry run — would open PR on branch {branch_name}",
                level="info",
            )
        )
        return {
            "status": "fixed",
            "output_branch": branch_name,
            "output_pr_url": f"https://github.com/{full_name}/pull/DRY-RUN",
            "audit_log": logs,
        }

    try:
        gh = GitHubClient(cfg)
        result = gh.create_fix_branch_and_pr(
            full_name=full_name,
            base_branch=base_branch,
            branch_name=branch_name,
            files=files_to_push,
            title=title,
            body=description,
            commit_message=commit_message,
        )
        logs.append(
            LogEntry.create("pr_opener", f"Opened fix PR: {result['pr_url']}")
        )
        return {
            "status": "fixed",
            "output_pr_url": result["pr_url"],
            "output_branch": result["branch"],
            "commit_sha": result.get("commit_sha"),
            "audit_log": logs,
        }
    except Exception as exc:
        logs.append(
            LogEntry.create("pr_opener", f"Failed to open PR: {exc}", level="error")
        )
        return {
            "status": "failed",
            "audit_log": logs,
        }
