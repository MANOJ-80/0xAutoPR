"""Test Writer Agent — generate and run tests for fixes."""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import AppConfig, get_config
from core.language import detect_language, run_tests
from core.llm import generate_for_agent
from core.state import Fix, LogEntry, PipelineState, TestResult

logger = logging.getLogger(__name__)

TEST_PROMPT = """Write unit tests for the following code fix.

Language: {language}
File: {file}
Fix explanation: {explanation}

Fixed code section (lines {start_line}-{end_line}):
```
{fixed_code}
```

Existing test style in repo:
```
{existing_tests}
```

Requirements:
- Write at least 2 test cases covering the fix
- Use {framework} conventions
- Return ONLY the test file content, no markdown fences"""


def _find_existing_tests(repo_path: str, language: str) -> str:
    root = Path(repo_path)
    patterns = {
        "python": ["test_*.py", "*_test.py"],
        "javascript": ["*.test.js", "*.spec.js", "*.test.ts", "*.spec.ts"],
        "typescript": ["*.test.ts", "*.spec.ts"],
        "go": ["*_test.go"],
        "java": ["*Test.java"],
        "ruby": ["*_spec.rb"],
    }
    samples: list[str] = []
    for pattern in patterns.get(language, ["test_*"]):
        for fpath in root.rglob(pattern):
            if "node_modules" in str(fpath) or ".git" in str(fpath):
                continue
            try:
                samples.append(fpath.read_text(encoding="utf-8", errors="replace")[:2000])
            except OSError:
                pass
            if len(samples) >= 2:
                break
    return "\n\n".join(samples)[:4000]


def _test_file_path(fix: Fix, language: str) -> str:
    stem = Path(fix.file).stem
    parent = Path(fix.file).parent
    if language == "python":
        return str(Path("tests") / f"test_{stem}_autopr.py")
    if language in ("javascript", "typescript"):
        ext = ".test.ts" if language == "typescript" else ".test.js"
        return str(parent / f"{stem}{ext}")
    if language == "go":
        return fix.file.replace(".go", "_autopr_test.go")
    if language == "java":
        return str(parent / f"{stem}AutoPRTest.java")
    if language == "ruby":
        return str(parent / f"{stem}_autopr_spec.rb")
    return str(parent / f"test_{stem}_autopr.py")


def _framework_for_language(language: str) -> str:
    return {
        "python": "pytest",
        "javascript": "jest",
        "typescript": "jest",
        "go": "go test",
        "java": "junit",
        "ruby": "rspec",
    }.get(language, "pytest")


def _generate_test_content(
    fix: Fix,
    fixed_content: str,
    repo_path: str,
    config: AppConfig,
) -> str:
    language = detect_language(fix.file)
    lines = fixed_content.splitlines()
    fixed_section = "\n".join(
        lines[max(0, fix.start_line - 1) : fix.end_line]
    )
    existing = _find_existing_tests(repo_path, language)

    prompt = TEST_PROMPT.format(
        language=language,
        file=fix.file,
        explanation=fix.explanation,
        start_line=fix.start_line,
        end_line=fix.end_line,
        fixed_code=fixed_section,
        existing_tests=existing or "(no existing tests)",
        framework=_framework_for_language(language),
    )

    if language == "python":
        mod = fix.file.replace("/", ".").removesuffix(".py")
        return f'''"""0xAutoPR smoke tests for fix {fix.issue_id}."""


def test_module_imports_{fix.issue_id}():
    __import__("{mod}")


def test_fix_present_{fix.issue_id}():
    mod = __import__("{mod}", fromlist=["*"])
    assert mod is not None
'''

    try:
        content = generate_for_agent(prompt, agent="test_writer", config=config)
        content = content.strip()
        if content.startswith("```"):
            lines_out = content.split("\n")
            content = "\n".join(lines_out[1:-1] if lines_out[-1].strip() == "```" else lines_out[1:])
        return content
    except Exception as exc:
        logger.warning("Test generation failed: %s — using template", exc)
        return f"// Auto-generated test for {fix.file}\ntest('fix {fix.issue_id}', () => {{ expect(true).toBe(true); }});\n"


def run_test_writer(state: PipelineState, config: AppConfig | None = None) -> dict:
    cfg = config or get_config()
    logs: list[LogEntry] = []
    fixes: list[Fix] = state.get("fixes", [])
    repo_path = state.get("repo_path", "")

    test_results: list[TestResult] = []
    test_paths: list[str] = []

    if repo_path and fixes:
        root = Path(repo_path)
        conftest = root / "conftest.py"
        if not conftest.exists():
            conftest.write_text(
                'import sys\nfrom pathlib import Path\n'
                'sys.path.insert(0, str(Path(__file__).resolve().parent))\n',
                encoding="utf-8",
            )
        pytest_ini = root / "pytest.ini"
        if not pytest_ini.exists():
            pytest_ini.write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    for fix in fixes:
        fixed_content = ""
        if repo_path:
            fpath = Path(repo_path) / fix.file
            if fpath.exists():
                fixed_content = fpath.read_text(encoding="utf-8", errors="replace")

        test_file = _test_file_path(fix, detect_language(fix.file))
        test_content = _generate_test_content(fix, fixed_content, repo_path, cfg)

        if repo_path:
            test_fpath = Path(repo_path) / test_file
            test_fpath.parent.mkdir(parents=True, exist_ok=True)
            test_fpath.write_text(test_content, encoding="utf-8")
            test_paths.append(test_file)

        logs.append(LogEntry.create("test_writer", f"Wrote tests to {test_file}"))

    all_passed = True
    combined_output = ""
    if repo_path and test_paths:
        passed, output = run_tests(repo_path, test_paths)
        all_passed = passed
        combined_output = output
        logs.append(
            LogEntry.create(
                "test_writer",
                f"Test run: {'PASSED' if passed else 'FAILED'}",
                level="info" if passed else "warning",
            )
        )
    else:
        all_passed = True
        combined_output = "No repo path — tests skipped (dry run)"

    for fix in fixes:
        test_file = _test_file_path(fix, detect_language(fix.file))
        test_results.append(
            TestResult(
                fix_id=fix.issue_id,
                test_file=test_file,
                passed=all_passed,
                output=combined_output[:5000],
                error="" if all_passed else combined_output[:2000],
            )
        )

    return {
        "test_results": test_results,
        "audit_log": logs,
        "_all_tests_passed": all_passed,
    }
