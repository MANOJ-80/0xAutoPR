#!/usr/bin/env python3
"""End-to-end agent test with real LLMs (no GitHub push required)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.fix_writer import run_fix_writer
from agents.patch_generator import run_patch_generator
from agents.review_agent import run_review_agent
from core.config import get_config
from core.state import ChangedFile, initial_state


BUGGY_CODE = '''\
def validate_token(token):
    if token == None:
        return False
    try:
        result = process(token)
    except:
        return False
    return result

api_key = "sk-hardcoded-secret-key-12345"

def process(data):
    return eval(data)
'''


def main() -> int:
    config = get_config()
    print("=== 0xAutoPR Real LLM Test ===")
    print(f"Groq:       {'yes' if config.has_groq() else 'NO'}")
    print(f"OpenRouter: {'yes' if config.openrouter_api_key else 'NO'}")
    print(f"Gemini:     {'yes' if config.has_gemini() else 'NO'}")

    if not config.has_groq() and not config.openrouter_api_key:
        print("ERROR: Set GROQ_API_KEY or OPENROUTER_API_KEY in .env")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "auth.py").write_text(BUGGY_CODE)

        state = initial_state(
            pr_url="https://github.com/test/demo/pull/1",
            pr_number=1,
            repo_url="https://github.com/test/demo",
            repo_full_name="test/demo",
            base_branch="main",
            head_branch="feature",
        )
        state["diff_raw"] = "diff --git a/auth.py b/auth.py"
        state["changed_files"] = [
            ChangedFile(
                path="auth.py",
                status="added",
                content=BUGGY_CODE,
                language="python",
            )
        ]
        state["repo_path"] = str(repo)

        print("\n[1/3] Review Agent (Groq)...")
        review = run_review_agent(state, config)
        issues = review["issues"]
        print(f"  Found {len(issues)} issues")
        for issue in issues[:5]:
            print(f"  - [{issue.severity}] {issue.title} ({issue.file}:{issue.line}) conf={issue.confidence:.2f}")

        state.update(review)

        print("\n[2/3] Fix Writer (Gemini→Groq fallback)...")
        fixes_result = run_fix_writer(state, config)
        fixes = fixes_result["fixes"]
        print(f"  Generated {len(fixes)} fixes")
        for fix in fixes[:3]:
            print(f"  - {fix.file}:{fix.start_line} — {fix.explanation[:80]}")

        state.update(fixes_result)

        print("\n[3/3] Patch Generator...")
        patch_result = run_patch_generator(state, config)
        patches = patch_result["patches"]
        print(f"  Generated {len(patches)} patches")
        for patch in patches:
            print(f"  - {patch.file}: {patch.commit_message}")
            print("    --- diff preview ---")
            for line in patch.unified_diff.splitlines()[:12]:
                print(f"    {line}")

    print("\n=== DONE — real LLM pipeline test complete ===")
    return 0 if issues else 1


if __name__ == "__main__":
    sys.exit(main())
