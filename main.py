#!/usr/bin/env python3
"""0xAutoPR CLI entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from agents.orchestrator import run_pipeline
from core.config import get_config
from triggers.webhook_server import main as run_server


def cmd_run(args: argparse.Namespace) -> int:
    result = run_pipeline(
        pr_url=args.pr_url,
        pr_number=args.pr_number,
        repo_url=args.repo_url,
        repo_full_name=args.repo_full_name,
        base_branch=args.base_branch,
        head_branch=args.head_branch,
    )
    print(json.dumps(
        {
            "status": result.get("status"),
            "confidence_score": result.get("confidence_score"),
            "issues_found": len(result.get("issues", [])),
            "fixes_applied": len(result.get("fixes", [])),
            "output_pr_url": result.get("output_pr_url"),
            "output_branch": result.get("output_branch"),
            "audit_log": [
                f"{log.agent.upper()}: {log.message}"
                for log in result.get("audit_log", [])
            ],
        },
        indent=2,
    ))
    return 0 if result.get("status") in ("fixed", "skipped") else 1


def cmd_serve(args: argparse.Namespace) -> int:
    run_server()
    return 0


def main() -> int:
    config = get_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="0xAutoPR — Automated PR Review & Auto-Fix")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run pipeline on a PR")
    run_p.add_argument("--pr-url", required=True)
    run_p.add_argument("--pr-number", type=int, required=True)
    run_p.add_argument("--repo-url", required=True)
    run_p.add_argument("--repo-full-name", required=True)
    run_p.add_argument("--base-branch", default="main")
    run_p.add_argument("--head-branch", required=True)
    run_p.set_defaults(func=cmd_run)

    serve_p = sub.add_parser("serve", help="Start webhook server")
    serve_p.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
