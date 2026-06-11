"""GitHub API wrapper with rate-limit backoff."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from github import Auth, Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from core.config import AppConfig, get_config

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class PRFileInfo:
    filename: str
    status: str
    patch: str
    content: str


class GitHubClient:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._gh = Github(auth=Auth.Token(self.config.github_token))

    def _with_backoff(self, fn, *args, **kwargs):
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except GithubException as exc:
                if exc.status == 403 and "rate limit" in str(exc).lower():
                    wait = 2 ** (attempt + 1) * 30
                    logger.warning("GitHub rate limit hit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("GitHub API rate limit exceeded after retries")

    def get_repo(self, full_name: str) -> Repository:
        return self._with_backoff(self._gh.get_repo, full_name)

    def get_pull_request(self, full_name: str, pr_number: int) -> PullRequest:
        repo = self.get_repo(full_name)
        return self._with_backoff(repo.get_pull, pr_number)

    def get_pr_diff(self, full_name: str, pr_number: int) -> str:
        pr = self.get_pull_request(full_name, pr_number)
        files = self._with_backoff(pr.get_files)
        parts: list[str] = []
        for f in files:
            parts.append(f"diff --git a/{f.filename} b/{f.filename}")
            if f.patch:
                parts.append(f.patch)
        return "\n".join(parts)

    def get_pr_files(self, full_name: str, pr_number: int) -> list[PRFileInfo]:
        pr = self.get_pull_request(full_name, pr_number)
        repo = self.get_repo(full_name)
        result: list[PRFileInfo] = []
        for f in self._with_backoff(pr.get_files):
            content = ""
            if f.status != "removed":
                try:
                    content_obj = repo.get_contents(f.filename, ref=pr.head.sha)
                    if hasattr(content_obj, "decoded_content"):
                        content = content_obj.decoded_content.decode("utf-8", errors="replace")
                except GithubException:
                    content = ""
            result.append(
                PRFileInfo(
                    filename=f.filename,
                    status=f.status,
                    patch=f.patch or "",
                    content=content,
                )
            )
        return result

    def create_fix_branch_and_pr(
        self,
        full_name: str,
        base_branch: str,
        branch_name: str,
        files: dict[str, str],
        title: str,
        body: str,
        commit_message: str,
    ) -> dict[str, str]:
        repo = self.get_repo(full_name)
        base_ref = self._with_backoff(repo.get_git_ref, f"heads/{base_branch}")
        base_sha = base_ref.object.sha

        try:
            self._with_backoff(repo.create_git_ref, f"refs/heads/{branch_name}", base_sha)
        except GithubException as exc:
            if exc.status != 422:
                raise

        for path, content in files.items():
            try:
                existing = repo.get_contents(path, ref=branch_name)
                self._with_backoff(
                    repo.update_file,
                    path,
                    commit_message,
                    content,
                    existing.sha,
                    branch=branch_name,
                )
            except GithubException:
                self._with_backoff(
                    repo.create_file,
                    path,
                    commit_message,
                    content,
                    branch=branch_name,
                )

        pr = self._with_backoff(
            repo.create_pull,
            title=title,
            body=body,
            head=branch_name,
            base=base_branch,
        )
        return {"pr_url": pr.html_url, "branch": branch_name, "commit_sha": pr.head.sha}

    def post_comment(self, full_name: str, pr_number: int, body: str) -> None:
        pr = self.get_pull_request(full_name, pr_number)
        self._with_backoff(pr.create_issue_comment, body)

    @staticmethod
    def parse_webhook_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        action = payload.get("action")
        if action not in ("opened", "synchronize", "reopened"):
            return None
        pr = payload.get("pull_request", {})
        repo = payload.get("repository", {})
        if not pr or not repo:
            return None
        return {
            "pr_url": pr.get("html_url", ""),
            "pr_number": pr.get("number", 0),
            "repo_url": repo.get("html_url", ""),
            "repo_full_name": repo.get("full_name", ""),
            "base_branch": pr.get("base", {}).get("ref", "main"),
            "head_branch": pr.get("head", {}).get("ref", ""),
            "installation_id": payload.get("installation", {}).get("id"),
        }


def build_pr_description(
    issues: list,
    fixes: list,
    test_results: list,
    confidence_score: float,
    original_pr_url: str,
) -> str:
    """Structured PR description template per spec."""
    lines = [
        "## 0xAutoPR Automated Fix",
        "",
        f"**Original PR:** {original_pr_url}",
        f"**Confidence Score:** {confidence_score:.2%}",
        "",
        "### Issues Found",
        "",
    ]
    for issue in issues:
        lines.append(
            f"- **[{issue.severity.upper()}]** `{issue.file}:{issue.line}` — {issue.title}"
        )
        lines.append(f"  - {issue.explanation}")
    lines.extend(["", "### Fixes Applied", ""])
    for fix in fixes:
        lines.append(f"- `{fix.file}` (lines {fix.start_line}-{fix.end_line}): {fix.explanation}")
    lines.extend(["", "### Tests Added", ""])
    for tr in test_results:
        status = "PASS" if tr.passed else "FAIL"
        lines.append(f"- `{tr.test_file}` — **{status}**")
    lines.extend(
        [
            "",
            "---",
            "*Generated by [0xAutoPR](https://github.com/0xAutoPR) — automated review & fix.*",
        ]
    )
    return "\n".join(lines)
