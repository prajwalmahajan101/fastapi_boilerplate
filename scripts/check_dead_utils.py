#!/usr/bin/env python3
"""Fail when any public symbol under ``src/core/`` has zero importers.

Utility atrophy is a recurring source of low-grade entropy in the
codebase — ``BaseSchema.alias_generator=to_camel``,
``ApiKeyRepository.touch_last_used``, and
``circuit_breaker.provider.reset_backend`` each lingered for several
review cycles without a caller before an explicit pass caught them.
This script gives the pre-commit hook a chance to flag the same shape
mechanically on every commit that touches ``src/core/`` — broader
than the historical scope of ``src/core/utils/`` so orphans hiding
in resilience providers, lifecycle helpers, exception modules, etc.
are caught alongside the utils-tree case.

For each ``.py`` file under ``src/core/`` (excluding ``__init__.py``),
the script:

* Parses the AST and collects every public, top-level name —
  functions, classes, async functions — that does not start with an
  underscore.
* Greps the rest of ``src/`` and ``scripts/`` for any import of that
  name from the dotted module path computed from the file path.
  The grep accepts both
  ``from src.core.<sub>.<mod> import X`` and
  ``import src.core.<sub>.<mod>`` (followed by ``.X``).
* Symbols re-exported via ``__all__`` in a sibling ``__init__.py``
  count as imported (because some external module reaches them via
  the package surface, not the leaf file).

Exit codes:
    ``0`` — every public symbol has at least one importer.
    ``1`` — one or more dead symbols; their dotted paths are printed.

Run manually via::

    python scripts/check_dead_utils.py

Wired as a local pre-commit hook in ``.pre-commit-config.yaml`` so
the same check runs on every commit that touches a file anywhere
under ``src/core/``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [ROOT / "src" / "core"]
SEARCH_ROOTS = [ROOT / "src", ROOT / "scripts"]

# Public symbols that are intentionally exported for use outside this
# repo (re-exported by ``src/core/__init__.py``). Listed by dotted
# module path + symbol name so a typo doesn't accidentally exempt the
# wrong file.
ALLOWLIST: set[tuple[str, str]] = {
    # Documented log-and-swallow helper for best-effort fan-out writes.
    # The example Item domain has no such writes, so no in-repo caller
    # exists; downstream projects pick this up for audit / tracking
    # rollups that must never fail the operation that preceded them.
    ("src.core.db.best_effort", "best_effort_atomic"),
}


def _module_dotted(path: Path) -> str:
    """Return the dotted import path for *path*.

    For ``ROOT/src/core/api_log/dispatch.py`` the result is
    ``src.core.api_log.dispatch``.

    Args:
        path: Absolute path to a Python source file under ``ROOT``.

    Returns:
        Dotted module path suitable for ``from ... import`` matching.
    """
    rel = path.resolve().relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def _public_symbols(path: Path) -> list[str]:
    """Return the public top-level names defined in *path* that have no in-file use.

    A name that is also referenced inside the same file (raised,
    instantiated, configured by string-name, etc.) is treated as live
    — the script targets symbols with zero callers, not symbols whose
    only callers are internal.

    Args:
        path: Python source file.

    Returns:
        Names of public top-level ``def`` / ``async def`` / ``class``
        declarations whose only reference inside the file is the
        declaration itself. Underscore-prefixed names are excluded.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            # The declaration itself appears once; require at least one
            # additional textual reference inside the same file to call
            # the symbol "used internally".
            if source.count(node.name) < 2:
                names.append(node.name)
    return names


def _has_importer(dotted_path: str, symbol: str, self_path: Path) -> bool:
    """Return ``True`` if any file under ``SEARCH_ROOTS`` imports *symbol*.

    Accepts:
        ``from <dotted_path> import <symbol>``
        ``from <dotted_path> import (..., <symbol>, ...)``
        ``import <dotted_path>`` followed by ``.<symbol>``

    The match is purely textual — false positives are possible but
    rare for symbols with descriptive names. False negatives (where a
    real importer is missed) are the failure mode this script is
    designed to surface, not to mask.

    Args:
        dotted_path: Defining module path (e.g.
            ``"src.core.api_log.dispatch"``).
        symbol: The public name defined in that module.
        self_path: Path of the defining file, skipped during the scan
            so an in-file reference is not mistaken for an importer.

    Returns:
        ``True`` when at least one source file under ``src/`` or
        ``scripts/`` references the symbol via either import form.
    """
    from_form = f"from {dotted_path} import"
    import_form = f"import {dotted_path}"
    self_resolved = self_path.resolve()

    for root in SEARCH_ROOTS:
        for py_file in root.rglob("*.py"):
            if py_file.resolve() == self_resolved:
                continue
            text = py_file.read_text(encoding="utf-8")
            if from_form in text and symbol in text:
                return True
            if import_form in text and f".{symbol}" in text:
                return True
    return False


def main() -> int:
    """Walk every ``SCAN_ROOTS`` entry, list dead public symbols, return 0/1.

    Returns:
        Process exit code — ``0`` on a clean run, ``1`` when one or
        more dead symbols were found.
    """
    dead: list[tuple[str, str]] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            dotted = _module_dotted(path)
            for symbol in _public_symbols(path):
                if (dotted, symbol) in ALLOWLIST:
                    continue
                if not _has_importer(dotted, symbol, path):
                    dead.append((dotted, symbol))

    if dead:
        print("Dead public symbols detected under src/core/:", file=sys.stderr)
        for dotted, symbol in dead:
            print(f"  {dotted}.{symbol}", file=sys.stderr)
        print(
            "\nEither wire a caller in the same commit, delete the symbol, "
            "or add (dotted_path, symbol) to ALLOWLIST in "
            "scripts/check_dead_utils.py with a comment explaining why.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
