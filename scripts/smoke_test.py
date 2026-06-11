#!/usr/bin/env python3
"""Quick smoke test for LLM providers and optional pipeline run."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from core.config import get_config
from core.llm import generate


def test_llm(name: str, prefer: str) -> bool:
    config = get_config()
    print(f"\n--- Testing {name} (prefer={prefer}) ---")
    try:
        reply = generate(
            "Reply with exactly: OK",
            prefer=prefer,
            config=config,
        )
        print(f"Response: {reply.strip()[:200]}")
        print(f"PASS: {name}")
        return True
    except Exception as exc:
        print(f"FAIL: {name} — {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", action="store_true", help="Run full pipeline on a PR")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--repo-full-name", help="e.g. owner/repo")
    args = parser.parse_args()

    config = get_config()
    print("Config check:")
    print(f"  Groq:       {'yes' if config.has_groq() else 'NO'}")
    print(f"  OpenRouter: {'yes' if config.openrouter_api_key else 'NO'}")
    print(f"  Gemini:     {'yes' if config.has_gemini() else 'NO'}")
    print(f"  GitHub:     {'yes' if config.has_github() else 'NO'}")
    print(f"  DRY_RUN:    {config.dry_run}")

    ok = True
    if config.has_groq():
        ok &= test_llm("Groq", "groq")
    if config.openrouter_api_key:
        ok &= test_llm("OpenRouter", "openrouter")

    if args.pipeline:
        if not config.has_github():
            print("\nERROR: Set GITHUB_TOKEN in .env for pipeline test")
            return 1
        if not args.pr_number or not args.repo_full_name:
            print("\nERROR: --pr-number and --repo-full-name required")
            return 1

        from agents.orchestrator import run_pipeline

        owner, repo = args.repo_full_name.split("/", 1)
        print(f"\n--- Running pipeline on {args.repo_full_name}#{args.pr_number} ---")
        result = run_pipeline(
            pr_url=f"https://github.com/{args.repo_full_name}/pull/{args.pr_number}",
            pr_number=args.pr_number,
            repo_url=f"https://github.com/{args.repo_full_name}",
            repo_full_name=args.repo_full_name,
            base_branch="main",
            head_branch="",
        )
        print(f"Status:          {result.get('status')}")
        print(f"Issues found:    {len(result.get('issues', []))}")
        print(f"Fixes generated: {len(result.get('fixes', []))}")
        print(f"Confidence:      {result.get('confidence_score', 0):.2%}")
        print(f"Output PR:       {result.get('output_pr_url')}")
        ok &= result.get("status") in ("fixed", "skipped", "escalated")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
