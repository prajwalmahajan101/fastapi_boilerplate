#!/usr/bin/env python3
"""Fail when any module under ``src/core/`` imports a non-core ``src`` package.

The repo-wide invariant (`src/core/CLAUDE.md`, "The one rule"): ``src.core``
must never import from ``src.common`` or any domain package
(``src.api`` / ``src.service`` / ``src.repository`` / ``src.schema`` /
``src.model`` / ``src.db`` / ``src.management``). Core reads config only
through ``core.runtime.get_settings()``; keeping the direction one-way
is what makes ``src.core`` liftable into the next project unchanged.

This script enforces the rule mechanically so it cannot regress
silently. It AST-walks every ``.py`` file under ``src/core/`` and fails
on the first offending import.

Exit codes:
    ``0`` — every core module imports only stdlib / third-party / ``src.core.*``.
    ``1`` — one or more forbidden imports; offending file:line and dotted
            module are printed.

Run manually via::

    python scripts/check_layering.py

Wired as a local pre-commit hook so the same check runs on every commit
that touches a file anywhere under ``src/core/``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "src" / "core"

# Any ``src.<x>`` other than ``src.core`` is forbidden inside core. Listed
# explicitly so a typo in a new top-level package is also caught.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "src.common",
    "src.api",
    "src.service",
    "src.repository",
    "src.schema",
    "src.model",
    "src.db",
    "src.management",
)


def _violations(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, dotted)`` for every forbidden import in *path*.

    Args:
        path: Absolute path to a Python source file under ``src/core/``.

    Returns:
        List of ``(line_number, dotted_module)`` tuples — empty when the
        file is clean.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in FORBIDDEN_PREFIXES:
                if node.module == prefix or node.module.startswith(prefix + "."):
                    out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in FORBIDDEN_PREFIXES:
                    if alias.name == prefix or alias.name.startswith(prefix + "."):
                        out.append((node.lineno, alias.name))
    return out


def main() -> int:
    """Walk ``src/core/`` and report forbidden imports.

    Returns:
        ``0`` when clean, ``1`` when any violation is found.
    """
    failed = False
    for path in sorted(CORE_ROOT.rglob("*.py")):
        for lineno, dotted in _violations(path):
            rel = path.relative_to(ROOT)
            print(f"{rel}:{lineno}: forbidden import in src.core/: {dotted}")
            failed = True
    if failed:
        print(
            "\nsrc.core must not import from src.common or any domain package "
            "(see src/core/CLAUDE.md). Move the shared value behind "
            "core.runtime.get_settings() or refactor the helper.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
