"""Prevent new oversized Python source files and growth of legacy exceptions."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

SOURCE_PREFIX = "src/quant_assistant/"
SOURCE_LINE_LIMIT = 900


def check_source_size(
    root: Path, baseline: dict[str, int], limit: int = SOURCE_LINE_LIMIT
) -> tuple[str, ...]:
    """Return violations; legacy entries are exact paths with fixed ceilings."""

    root = root.resolve()
    source_root = root / "src/quant_assistant"
    if not source_root.is_dir():
        return ("Python source tree is missing: src/quant_assistant",)
    issues: list[str] = []
    ceilings: dict[str, int] = {}
    for name, cap in baseline.items():
        if (
            not isinstance(name, str)
            or not name.startswith(SOURCE_PREFIX)
            or not name.endswith(".py")
            or any(part in name for part in ("..", "\\", "*", "?", "[", "]"))
            or PurePosixPath(name).as_posix() != name
            or type(cap) is not int
            or cap <= limit
        ):
            issues.append(f"Invalid source-size exception: {name}")
            continue
        ceilings[name] = cap
        if not (root / name).is_file():
            issues.append(f"Remove missing source-size exception: {name}")

    paths = sorted(source_root.rglob("*.py"))
    if not paths:
        issues.append("Python source tree has no .py files")
    for path in paths:
        name = path.relative_to(root).as_posix()
        if not path.resolve().is_relative_to(source_root.resolve()):
            issues.append(f"Source file points outside source tree: {name}")
            continue
        try:
            count = len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            issues.append(f"Cannot count source lines: {name}")
            continue
        cap = ceilings.get(name, limit)
        if count > cap:
            issues.append(f"{name}: {count} lines exceeds ceiling {cap}")
        if name in ceilings and count <= limit:
            issues.append(f"Remove retired source-size exception: {name} ({count} lines)")
    return tuple(issues)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        baseline = json.loads((root / "scripts/source-size-baseline.json").read_text("utf-8"))
        if not isinstance(baseline, dict):
            raise ValueError("invalid baseline")
    except (OSError, UnicodeError, ValueError):
        print("Source-size baseline is missing or invalid")
        return 1
    issues = check_source_size(root, baseline)
    if issues:
        print("Source-size gate failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"Source-size gate passed: limit {SOURCE_LINE_LIMIT}, {len(baseline)} legacy ceilings")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
