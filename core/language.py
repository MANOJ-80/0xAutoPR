"""Language detection and test runner selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


EXTENSION_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
}


def detect_language(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(ext, "unknown")


def detect_repo_test_framework(repo_path: str) -> dict[str, str]:
    """Detect primary test framework from repo markers."""
    root = Path(repo_path)
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        return {"language": "python", "runner": "pytest", "command": "pytest -q"}
    if (root / "package.json").exists():
        pkg = (root / "package.json").read_text(encoding="utf-8", errors="replace")
        if "jest" in pkg:
            return {"language": "javascript", "runner": "jest", "command": "npx jest --passWithNoTests"}
        if "vitest" in pkg:
            return {"language": "javascript", "runner": "vitest", "command": "npx vitest run"}
        return {"language": "javascript", "runner": "npm test", "command": "npm test --if-present"}
    if (root / "go.mod").exists():
        return {"language": "go", "runner": "go test", "command": "go test ./..."}
    if (root / "pom.xml").exists() or (root / "build.gradle").exists():
        return {"language": "java", "runner": "maven", "command": "mvn test -q"}
    if (root / "Gemfile").exists():
        return {"language": "ruby", "runner": "rspec", "command": "bundle exec rspec"}
    return {"language": "python", "runner": "pytest", "command": "pytest -q"}


def run_tests(
    repo_path: str,
    test_paths: Optional[list[str]] = None,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Run tests in a sandboxed subprocess."""
    framework = detect_repo_test_framework(repo_path)
    cmd = framework["command"]
    if test_paths and framework["runner"] == "pytest":
        cmd = "pytest -q " + " ".join(test_paths)
    elif test_paths and framework["runner"] == "jest":
        cmd = "npx jest --passWithNoTests " + " ".join(test_paths)

    env = os.environ.copy()
    env["PYTHONPATH"] = repo_path + os.pathsep + env.get("PYTHONPATH", "")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out"
    except Exception as exc:
        return False, str(exc)
