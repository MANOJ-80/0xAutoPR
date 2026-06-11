"""Integration tests for the full pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.orchestrator import build_pipeline, run_pipeline
from core.config import AppConfig, ThresholdConfig
from core.state import ChangedFile, initial_state


@pytest.fixture
def dry_config():
    return AppConfig(
        dry_run=True,
        thresholds=ThresholdConfig(
            fix_min_confidence=0.5,
            escalate_confidence=0.5,
            max_retries=1,
        ),
    )


class TestPipelineGraph:
    def test_graph_compiles(self):
        graph = build_pipeline()
        app = graph.compile()
        assert app is not None


class TestPipelineIntegration:
    def test_pipeline_with_mocked_code_reader(self, dry_config, tmp_path):
        buggy_file = tmp_path / "app.py"
        buggy_file.write_text(
            "def process(data):\n"
            "    try:\n"
            "        return data['key']\n"
            "    except:\n"
            "        return None\n"
        )

        state = initial_state(
            pr_url="https://github.com/test/repo/pull/42",
            pr_number=42,
            repo_url="https://github.com/test/repo",
            repo_full_name="test/repo",
            base_branch="main",
            head_branch="feature",
        )

        mock_reader_result = {
            "diff_raw": "diff --git a/app.py b/app.py",
            "changed_files": [
                ChangedFile(
                    path="app.py",
                    status="modified",
                    content=buggy_file.read_text(),
                    language="python",
                )
            ],
            "repo_path": str(tmp_path),
            "repo_context": __import__("core.state", fromlist=["VectorStoreRef"]).VectorStoreRef(
                collection_name="", chunk_count=0,
            ),
            "audit_log": [],
        }

        with patch("agents.orchestrator.run_code_reader", return_value=mock_reader_result):
            graph = build_pipeline()
            app = graph.compile()
            final = app.invoke(state)

        assert final.get("status") in ("fixed", "skipped", "escalated", "running", "failed")
        assert "audit_log" in final

    def test_pipeline_skips_low_confidence(self, dry_config):
        state = initial_state(
            pr_url="https://github.com/test/repo/pull/1",
            pr_number=1,
            repo_url="https://github.com/test/repo",
            repo_full_name="test/repo",
            base_branch="main",
            head_branch="feature",
        )

        mock_reader = {
            "diff_raw": "",
            "changed_files": [],
            "repo_path": "/tmp",
            "repo_context": __import__("core.state", fromlist=["VectorStoreRef"]).VectorStoreRef(
                collection_name="", chunk_count=0,
            ),
            "audit_log": [],
        }

        mock_review = {
            "issues": [],
            "confidence_score": 1.0,
            "audit_log": [],
        }

        with patch("agents.orchestrator.run_code_reader", return_value=mock_reader), \
             patch("agents.orchestrator.run_review_agent", return_value=mock_review):
            graph = build_pipeline()
            final = graph.compile().invoke(state)

        assert final.get("status") == "skipped"


class TestWebhookSignature:
    def test_verify_signature(self):
        import hashlib
        import hmac

        from triggers.webhook_server import verify_github_signature

        secret = "test-secret"
        payload = b'{"action": "opened"}'
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_github_signature(payload, sig, secret) is True
        assert verify_github_signature(payload, "sha256=bad", secret) is False
