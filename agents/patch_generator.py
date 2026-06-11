"""Patch Generator Agent — produce git-ready unified diffs."""

from __future__ import annotations

import ast
import difflib
import logging
from pathlib import Path

from core.config import AppConfig, get_config
from core.llm import generate_for_agent, is_llm_available
from core.state import Fix, LogEntry, Patch, PipelineState

logger = logging.getLogger(__name__)


def _valid_python(content: str) -> bool:
    try:
        ast.parse(content)
        return True
    except SyntaxError:
        return False


def _apply_fix_to_content(content: str, fix: Fix) -> str | None:
    lines = content.splitlines(keepends=True)
    if not lines and content and not content.endswith("\n"):
        lines = [content]

    if fix.original_lines:
        original_block = "\n".join(fix.original_lines)
        if original_block in content:
            replacement = "\n".join(fix.replacement_lines)
            candidate = content.replace(original_block, replacement, 1)
            if fix.file.endswith(".py") and not _valid_python(candidate):
                return None
            return candidate
        start = max(0, fix.start_line - 1)
        end = fix.end_line
        actual = "\n".join(content.splitlines()[start:end])
        if actual.strip() != original_block.strip():
            return None

    start = max(0, fix.start_line - 1)
    end = fix.end_line
    replacement_lines = [
        l + ("\n" if not l.endswith("\n") else "") for l in fix.replacement_lines
    ]
    candidate = "".join(lines[:start] + replacement_lines + lines[end:])
    if fix.file.endswith(".py") and not _valid_python(candidate):
        return None
    return candidate


def _generate_commit_message(fixes: list[Fix], config: AppConfig) -> str:
    if len(fixes) == 1:
        prefix = "fix"
        scope = Path(fixes[0].file).stem
        return f"{prefix}({scope}): {fixes[0].explanation[:72]}"

    prompt = f"""Generate a conventional commit message for these fixes:
{chr(10).join(f'- {f.explanation}' for f in fixes[:5])}

Return ONLY the commit message line (e.g. fix(auth): handle null token)."""

    if is_llm_available():
        try:
            msg = generate_for_agent(prompt, agent="patch_generator", config=config).strip().split("\n")[0]
            return msg[:100]
        except Exception:
            pass
    return f"fix: resolve {len(fixes)} issues found by 0xAutoPR"


def run_patch_generator(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []
    fixes: list[Fix] = state.get("fixes", [])
    repo_path = state.get("repo_path", "")

    file_fixes: dict[str, list[Fix]] = {}
    for fix in fixes:
        file_fixes.setdefault(fix.file, []).append(fix)

    patches: list[Patch] = []
    modified_files: dict[str, str] = {}

    for file_path, file_fix_list in file_fixes.items():
        original_content = ""
        if repo_path:
            fpath = Path(repo_path) / file_path
            if fpath.exists():
                original_content = fpath.read_text(encoding="utf-8", errors="replace")

        if not original_content:
            for cf in state.get("changed_files", []):
                if cf.path == file_path:
                    original_content = cf.content
                    break

        new_content = original_content
        applied = 0
        for fix in sorted(file_fix_list, key=lambda f: f.start_line, reverse=True):
            result = _apply_fix_to_content(new_content, fix)
            if result is None:
                logs.append(
                    LogEntry.create(
                        "patch_generator",
                        f"Skipped invalid fix for {file_path}:{fix.start_line}",
                        level="warning",
                    )
                )
                continue
            new_content = result
            applied += 1

        if "os.environ" in new_content and "import os" not in new_content:
            new_content = "import os\n\n" + new_content

        if applied == 0 or new_content == original_content:
            logs.append(
                LogEntry.create(
                    "patch_generator",
                    f"No diff produced for {file_path}",
                    level="warning",
                )
            )
            continue

        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
        unified = "".join(diff)
        if not unified:
            continue

        commit_msg = _generate_commit_message(file_fix_list, cfg)
        patches.append(
            Patch(
                file=file_path,
                unified_diff=unified,
                commit_message=commit_msg,
                fix_ids=[f.issue_id for f in file_fix_list],
            )
        )
        modified_files[file_path] = new_content

        if repo_path:
            fpath = Path(repo_path) / file_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(new_content, encoding="utf-8")

        logs.append(LogEntry.create("patch_generator", f"Generated patch for {file_path}"))

    return {
        "patches": patches,
        "audit_log": logs,
        "_modified_files": modified_files,
    }
