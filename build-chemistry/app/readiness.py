"""Credential-free deployment readiness and source secret checks."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    Path("app/templates/base.html"),
    Path("app/static/app.css"),
    Path("uv.lock"),
)
IGNORED_PARTS = {".git", ".venv", ".pytest_cache", "__pycache__"}
TEXT_SUFFIXES = {".css", ".html", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
)
FORBIDDEN_NAMES = {".env", "credentials.json"}


def readiness_errors(root: Path = ROOT) -> list[str]:
    """Return safe labels only; never copy matching credential material."""
    errors = [
        f"missing required file: {relative}"
        for relative in REQUIRED_PATHS
        if not (root / relative).is_file()
    ]
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    paths = (
        (root / item for item in tracked.stdout.decode().split("\0") if item)
        if tracked.returncode == 0
        else root.rglob("*")
    )
    for path in paths:
        if not path.is_file():
            continue
        if any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() == ".log":
            errors.append(f"runtime or credential file in source tree: {path.relative_to(root)}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            errors.append(f"possible credential in: {path.relative_to(root)}")
    return errors


def main() -> int:
    errors = readiness_errors()
    if errors:
        print("Readiness failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Readiness passed: resources present; no high-confidence credentials found in source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())