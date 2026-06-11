"""Unit tests for individual agents."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agents.fix_writer import run_fix_writer
from agents.patch_generator import run_patch_generator
from agents.review_agent import _heuristic_review, _rank_issues
from agents.test_writer import _test_file_path
from core.config import AppConfig, ThresholdConfig
from core.language import detect_language, detect_repo_test_framework
from core.state import ChangedFile, Issue, initial_state


@pytest.fixture
def sample_state():
    return initial_state(
        pr_url="https://github.com/test/repo/pull/1",
        pr_number=1,
        repo_url="https://github.com/test/repo",
        repo_full_name="test/repo",
        base_branch="main",
        head_branch="feature",
    )


@pytest.fixture
def config():
    return AppConfig(
        dry_run=True,
        thresholds=ThresholdConfig(fix_min_confidence=0.5, escalate_confidence=0.75),
    )


class TestLanguage:
    def test_detect_python(self):
        assert detect_language("src/main.py") == "python"

    def test_detect_typescript(self):
        assert detect_language("src/app.tsx") == "typescript"

    def test_detect_go(self):
        assert detect_language("cmd/server/main.go") == "go"


class TestReviewAgent:
    def test_heuristic_finds_bare_except(self):
        cf = ChangedFile(
            path="bad.py",
            status="modified",
            content="def foo():\n    try:\n        pass\n    except:\n        pass\n",
            language="python",
        )
        issues = _heuristic_review([cf], "bug")
        assert len(issues) >= 1
        assert any("except" in i["title"].lower() or "except" in i["explanation"].lower() for i in issues)

    def test_heuristic_finds_security_issues(self):
        cf = ChangedFile(
            path="secrets.py",
            status="added",
            content='api_key = "sk-12345"\n',
            language="python",
        )
        issues = _heuristic_review([cf], "security")
        assert len(issues) >= 1

    def test_rank_issues_by_severity(self):
        issues = [
            Issue(
                id="1", category="bug", severity="low", file="a.py", line=1,
                title="low", explanation="", confidence=0.9,
            ),
            Issue(
                id="2", category="bug", severity="critical", file="a.py", line=2,
                title="critical", explanation="", confidence=0.5,
            ),
        ]
        ranked = _rank_issues(issues)
        assert ranked[0].severity == "critical"


class TestFixWriter:
    def test_generates_fix_for_heuristic_issue(self, sample_state, config, tmp_path):
        sample_state["changed_files"] = [
            ChangedFile(
                path="bad.py",
                status="modified",
                content="x = None\nif x == None:\n    pass\n",
                language="python",
            )
        ]
        sample_state["issues"] = [
            Issue(
                id="abc123",
                category="bug",
                severity="medium",
                file="bad.py",
                line=2,
                title="Use is None",
                explanation="Compare with is None",
                confidence=0.8,
                snippet="if x == None:",
            )
        ]
        sample_state["repo_path"] = str(tmp_path)
        (tmp_path / "bad.py").write_text("x = None\nif x == None:\n    pass\n")

        result = run_fix_writer(sample_state, config)
        assert len(result["fixes"]) >= 1


class TestPatchGenerator:
    def test_produces_unified_diff(self, sample_state, config, tmp_path):
        content = "x = None\nif x == None:\n    pass\n"
        (tmp_path / "bad.py").write_text(content)
        sample_state["repo_path"] = str(tmp_path)
        sample_state["fixes"] = [
            __import__("core.state", fromlist=["Fix"]).Fix(
                issue_id="abc",
                file="bad.py",
                original_lines=["if x == None:"],
                replacement_lines=["if x is None:"],
                start_line=2,
                end_line=2,
                explanation="Fix None comparison",
                confidence=0.85,
            )
        ]
        result = run_patch_generator(sample_state, config)
        assert len(result["patches"]) == 1
        assert "---" in result["patches"][0].unified_diff


class TestTestWriter:
    def test_test_file_path_python(self):
        path = _test_file_path(
            __import__("core.state", fromlist=["Fix"]).Fix(
                issue_id="x", file="src/utils.py",
                original_lines=[], replacement_lines=[],
                start_line=1, end_line=1, explanation="",
            ),
            "python",
        )
        assert "test_utils_autopr.py" in path


class TestFrameworkDetection:
    def test_detect_pytest(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        fw = detect_repo_test_framework(str(tmp_path))
        assert fw["runner"] == "pytest"

    def test_detect_jest(self, tmp_path):
        (tmp_path / "package.json").write_text('{"devDependencies": {"jest": "^29.0.0"}}')
        fw = detect_repo_test_framework(str(tmp_path))
        assert fw["runner"] == "jest"
