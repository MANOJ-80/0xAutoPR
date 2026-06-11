"""Flask webhook server for GitHub/GitLab/Bitbucket PR events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from typing import Any

from flask import Flask, jsonify, request

from agents.orchestrator import run_pipeline
from core.config import get_config
from core.github_client import GitHubClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _run_pipeline_async(event: dict[str, Any]) -> None:
    try:
        result = run_pipeline(
            pr_url=event["pr_url"],
            pr_number=event["pr_number"],
            repo_url=event["repo_url"],
            repo_full_name=event["repo_full_name"],
            base_branch=event["base_branch"],
            head_branch=event["head_branch"],
        )
        logger.info(
            "Pipeline completed: status=%s pr=%s",
            result.get("status"),
            result.get("output_pr_url"),
        )
    except Exception:
        logger.exception("Pipeline failed for PR #%s", event.get("pr_number"))


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "0xAutoPR"})


@app.route("/webhook/github", methods=["POST"])
def github_webhook():
    config = get_config()
    payload_bytes = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_github_signature(payload_bytes, signature, config.github_webhook_secret):
        logger.warning("Invalid webhook signature")
        return jsonify({"error": "invalid signature"}), 401

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "pull_request":
        return jsonify({"status": "ignored", "reason": f"event type {event_type}"}), 200

    payload = request.get_json(silent=True) or {}
    parsed = GitHubClient.parse_webhook_payload(payload)
    if not parsed:
        return jsonify({"status": "ignored", "reason": "action not handled"}), 200

    thread = threading.Thread(target=_run_pipeline_async, args=(parsed,), daemon=True)
    thread.start()

    return jsonify({"status": "accepted", "pr_number": parsed["pr_number"]}), 202


@app.route("/webhook/gitlab", methods=["POST"])
def gitlab_webhook():
    payload = request.get_json(silent=True) or {}
    obj_attrs = payload.get("object_attributes", {})
    action = obj_attrs.get("action", "")

    if action not in ("open", "update", "reopen"):
        return jsonify({"status": "ignored"}), 200

    project = payload.get("project", {})
    event = {
        "pr_url": obj_attrs.get("url", ""),
        "pr_number": obj_attrs.get("iid", 0),
        "repo_url": project.get("web_url", ""),
        "repo_full_name": project.get("path_with_namespace", ""),
        "base_branch": obj_attrs.get("target_branch", "main"),
        "head_branch": obj_attrs.get("source_branch", ""),
    }

    thread = threading.Thread(target=_run_pipeline_async, args=(event,), daemon=True)
    thread.start()
    return jsonify({"status": "accepted"}), 202


@app.route("/webhook/bitbucket", methods=["POST"])
def bitbucket_webhook():
    payload = request.get_json(silent=True) or {}
    pr = payload.get("pullrequest", {})
    repo = payload.get("repository", {})

    event = {
        "pr_url": pr.get("links", {}).get("html", {}).get("href", ""),
        "pr_number": pr.get("id", 0),
        "repo_url": repo.get("links", {}).get("html", {}).get("href", ""),
        "repo_full_name": repo.get("full_name", ""),
        "base_branch": pr.get("destination", {}).get("branch", {}).get("name", "main"),
        "head_branch": pr.get("source", {}).get("branch", {}).get("name", ""),
    }

    thread = threading.Thread(target=_run_pipeline_async, args=(event,), daemon=True)
    thread.start()
    return jsonify({"status": "accepted"}), 202


def main():
    config = get_config()
    logging.getLogger().setLevel(config.log_level)
    logger.info("Starting 0xAutoPR webhook server on %s:%d", config.host, config.port)
    app.run(host=config.host, port=config.port, debug=False)


if __name__ == "__main__":
    main()
