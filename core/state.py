"""Shared pipeline state schema for LangGraph agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class ChangedFile(BaseModel):
    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    patch: str = ""
    content: str = ""
    language: str = ""


class VectorStoreRef(BaseModel):
    collection_name: str
    chunk_count: int = 0
    persist_dir: str = ""


class Issue(BaseModel):
    id: str
    category: Literal["bug", "security", "quality"]
    severity: Literal["critical", "high", "medium", "low"]
    file: str
    line: int
    end_line: Optional[int] = None
    title: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    snippet: str = ""


class Fix(BaseModel):
    issue_id: str
    file: str
    original_lines: list[str]
    replacement_lines: list[str]
    start_line: int
    end_line: int
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class Patch(BaseModel):
    file: str
    unified_diff: str
    commit_message: str
    fix_ids: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    fix_id: str
    test_file: str
    passed: bool
    output: str = ""
    error: str = ""


class LogEntry(BaseModel):
    timestamp: str
    agent: str
    level: Literal["info", "warning", "error", "debug"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        agent: str,
        message: str,
        level: Literal["info", "warning", "error", "debug"] = "info",
        **data: Any,
    ) -> "LogEntry":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            level=level,
            message=message,
            data=data,
        )


PipelineStatus = Literal["running", "fixed", "escalated", "skipped", "failed"]


def merge_audit_logs(
    existing: list[LogEntry], new: list[LogEntry]
) -> list[LogEntry]:
    return existing + new


def merge_issues(existing: list[Issue], new: list[Issue]) -> list[Issue]:
    seen = {i.id for i in existing}
    merged = list(existing)
    for issue in new:
        if issue.id not in seen:
            merged.append(issue)
            seen.add(issue.id)
    return merged


class PipelineState(TypedDict, total=False):
    """LangGraph shared state contract."""

    pr_url: str
    pr_number: int
    repo_url: str
    repo_full_name: str
    base_branch: str
    head_branch: str
    diff_raw: str
    changed_files: list[ChangedFile]
    repo_context: VectorStoreRef
    repo_path: str
    issues: Annotated[list[Issue], merge_issues]
    fixes: list[Fix]
    patches: list[Patch]
    test_results: list[TestResult]
    confidence_score: float
    retry_count: int
    status: PipelineStatus
    output_pr_url: Optional[str]
    output_branch: Optional[str]
    commit_sha: Optional[str]
    audit_log: Annotated[list[LogEntry], merge_audit_logs]
    platform: Literal["github", "gitlab", "bitbucket"]
    installation_id: Optional[int]


def initial_state(
    *,
    pr_url: str,
    pr_number: int,
    repo_url: str,
    repo_full_name: str,
    base_branch: str,
    head_branch: str,
    platform: Literal["github", "gitlab", "bitbucket"] = "github",
) -> PipelineState:
    return PipelineState(
        pr_url=pr_url,
        pr_number=pr_number,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        base_branch=base_branch,
        head_branch=head_branch,
        diff_raw="",
        changed_files=[],
        repo_context=VectorStoreRef(collection_name="", persist_dir=""),
        repo_path="",
        issues=[],
        fixes=[],
        patches=[],
        test_results=[],
        confidence_score=0.0,
        retry_count=0,
        status="running",
        output_pr_url=None,
        output_branch=None,
        commit_sha=None,
        audit_log=[],
        platform=platform,
        installation_id=None,
    )
