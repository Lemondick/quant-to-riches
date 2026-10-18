"""Repository secret scanner shared by the CLI and verification scripts."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        ".test-tmp",
        ".uv-cache",
        ".venv",
        ".worktrees",
        "__pycache__",
        "backups",
        "data",
        "exports",
        "htmlcov",
    }
)
MAX_TEXT_BYTES = 2_000_000
PLACEHOLDERS = frozenset(
    {
        "",
        "<api-key>",
        "<placeholder>",
        "<redacted>",
        "<set-in-environment>",
        "<set-on-server>",
        "changeme",
        "dummy",
        "example",
        "fake",
        "ignored",
        "none",
        "placeholder",
        "redacted",
        "secret",
        "test",
        "token",
        "tushare_token",
        "your-api-key",
        "your-token",
    }
)

ASSIGNMENT_PATTERNS = (
    re.compile(
        r"(?i)(?<![\w.])(OPENAI_API_KEY|TUSHARE_TOKEN)\b\s*=(?!=)\s*"
        r"[\"']?([^\s\"'`;#]+)"
    ),
    re.compile(
        r"(?i)(?<![\w.])((?:ALIPAY|BROKER)[A-Z0-9_]*(?:PASSWORD|COOKIE|TOKEN))\b"
        r"\s*=(?!=)\s*[\"']?([^\s\"'`;#]+)"
    ),
)
# Credentials embedded in a connection string, e.g. a libpq keyword DSN assigned
# to a variable, or a ``scheme://user:secret@host/db`` URL.
#
# The keyword form requires a sibling DSN key (host / dbname / user) inside the
# same quoted string. That is what separates a leaked connection string from an
# ordinary keyword argument (a request body field named password, and so on).
CONNECTION_STRING_PATTERNS = (
    re.compile(
        r"(?i)[\"'][^\"'\n]*?(?:host|dbname|database|user|username)\s*=\s*[^\s\"'`;&]+"
        r"[^\"'\n]*?(?:password|pwd)\s*=\s*([^\s\"'`;&,)]+)"
    ),
    re.compile(
        r"(?i)[\"'][^\"'\n]*?(?:password|pwd)\s*=\s*[^\s\"'`;&,)]+"
        r"[^\"'\n]*?(?:host|dbname|database)\s*="
    ),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@\"'`]+)@"),
)
LITERAL_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
)
QUOTED_STRING = re.compile(r"""(["'])(?:\\.|(?!\1)[^\\\n])*\1""")


def scan_tracked_files_for_secrets(repo_root: str | Path | None = None) -> list[str]:
    """Return stable ``path:line:kind`` findings for repository text files."""

    root = Path(repo_root or Path.cwd()).resolve()
    findings: list[str] = []
    for path in _repository_files(root):
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern in ASSIGNMENT_PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(2).strip().rstrip(",)")
                    if _is_placeholder(value):
                        continue
                    findings.append(f"{relative}:{line_number}:{match.group(1).lower()}")
            for pattern in CONNECTION_STRING_PATTERNS:
                # Never treat a closing quote as the start of another string,
                # e.g. a C# URL followed by an anonymous request body.
                candidates = (
                    [line]
                    if pattern is CONNECTION_STRING_PATTERNS[-1]
                    else [literal.group(0) for literal in QUOTED_STRING.finditer(line)]
                )
                for match in (match for text in candidates for match in pattern.finditer(text)):
                    groups = match.groups()
                    # Some patterns locate the DSN without capturing the secret.
                    value = (groups[0] if groups and groups[0] else match.group(0)).strip()
                    value = value.strip().rstrip(",;)")
                    if _is_placeholder(value):
                        continue
                    findings.append(f"{relative}:{line_number}:connection_password")
            for kind, pattern in LITERAL_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number}:{kind}")
    return sorted(set(findings))


def _repository_files(root: Path) -> Iterable[Path]:
    candidates = _git_candidates(root)
    if candidates is None:
        candidates = (path for path in root.rglob("*") if path.is_file())
    for path in candidates:
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in EXCLUDED_PARTS for part in relative_parts):
            continue
        yield path


def _git_candidates(root: Path) -> tuple[Path, ...] | None:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    names = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return tuple(root / name for name in names if name and (root / name).is_file())


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    return (
        normalized in PLACEHOLDERS
        or (normalized.startswith("<") and normalized.endswith(">"))
        or normalized.startswith("${")
        or normalized.startswith("$env:")
        or normalized.startswith("your_")
    )


__all__ = ["scan_tracked_files_for_secrets"]
