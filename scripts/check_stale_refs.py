#!/usr/bin/env python3
"""Fail the commit if any documented stale reference appears in docs / source.

This script is the corrective answer to the doc-rot pattern that
recurred across several review cycles on the sibling Django project:
the same renamed / deleted symbol kept showing up in documentation
after the code had moved on. Manual discipline (a checklist in the
PR template, a CHANGELOG entry, a grep before push) was empirically
not enough. So the responsibility now lives in a pre-commit hook
that runs on every commit and refuses to let the rot land.

Scope
-----

Scans documentation surfaces (``docs/``, ``CLAUDE.md``, ``README.md``,
``AGENTS.md``, per-module ``CLAUDE.md`` files under ``src/`` /
``tests/``) **and** source files under ``src/``, ``tests/``,
``scripts/``, ``alembic/`` (``*.py``). Tests catch the symbol-rename
cases; this hook also catches name lineage that escapes the test
surface — donor-project literals embedded in module docstrings,
cache key prefixes, settings strings, fixture email addresses.

Skips
-----

* ``.code_review/`` — a living record of past review state,
  intentionally allowed to reference old names.
* ``CHANGELOG.md`` — a rename entry must use the old name to describe
  the change.
* ``alembic/versions/`` — Alembic migrations preserve the old field
  names by design; renaming a column is itself encoded as a
  migration that mentions both names.

A line containing the marker ``# stale-refs: allow`` is skipped, so a
legitimate occurrence (deprecation shim, intentional historical
reference) can be carved out without dropping the whole file.

Patterns + replacement hints live in ``scripts/stale_refs.yaml`` so a
rename commit can append the old symbol in the same PR.

Exit codes
----------

* ``0`` — every scanned line is clean.
* ``1`` — at least one pattern matched; each hit is printed with
  ``path:line: <line text>`` plus the configured replacement hint
  so the author can fix and re-commit without consulting external
  docs.
* ``2`` — configuration error (missing manifest, PyYAML not
  installed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "check_stale_refs.py: PyYAML is required. Install it with "
        "`pip install pyyaml` (the pre-commit hook installs it into its "
        "own virtualenv via additional_dependencies).",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "stale_refs.yaml"

# Documentation + code surfaces scanned. Globs are evaluated from REPO_ROOT.
INCLUDE_GLOBS = [
    "docs/**/*.md",
    "CLAUDE.md",
    "README.md",
    "AGENTS.md",
    "src/**/CLAUDE.md",
    "tests/**/CLAUDE.md",
    "src/**/*.py",
    "tests/**/*.py",
    "scripts/**/*.py",
    "alembic/**/*.py",
]

# Per-line allow marker. If the line contains this substring the line
# is skipped. Lets a single legitimate occurrence opt out without
# dropping scope on the whole file (and without polluting the manifest
# with file-level exclusions).
ALLOW_MARKER = "# stale-refs: allow"

# Always-ignored paths. ``.code_review/`` is a living history of prior
# review state and is *expected* to mention old symbols. ``CHANGELOG.md``
# is allowed because a rename entry must use the old name to describe
# the change. ``docs/decisions/`` (ADRs) are historical records by
# definition: an ADR documenting "X was removed; we now do Y" must
# reference X verbatim in its decision/context/alternatives prose.
EXCLUDE_PREFIXES = (
    ".code_review/",
    "CHANGELOG.md",
    "docs/decisions/",
)

# Path substrings that mark a scanned file as historical-record. Alembic
# migrations preserve old column / index names by design — renaming a
# field is itself encoded as a migration that mentions both names.
EXCLUDE_SUBSTRINGS = ("/alembic/versions/",)


def load_manifest() -> list[tuple[re.Pattern[str], str]]:
    """Read ``scripts/stale_refs.yaml`` and compile its regex patterns.

    Returns:
        A list of ``(compiled_pattern, replacement_hint)`` tuples in
        manifest order.
    """
    if not MANIFEST.exists():
        print(
            f"check_stale_refs.py: manifest not found at {MANIFEST}",
            file=sys.stderr,
        )
        sys.exit(2)
    raw = yaml.safe_load(MANIFEST.read_text()) or {}
    patterns = raw.get("patterns") or []
    compiled: list[tuple[re.Pattern[str], str]] = []
    for entry in patterns:
        pattern = entry.get("pattern")
        hint = entry.get("replacement", "")
        if not pattern:
            continue
        compiled.append((re.compile(pattern), hint))
    return compiled


def discover_files() -> list[Path]:
    """Resolve :data:`INCLUDE_GLOBS` to a sorted file list, minus exclusions."""
    files: set[Path] = set()
    for glob in INCLUDE_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
                continue
            if any(sub in f"/{rel}" for sub in EXCLUDE_SUBSTRINGS):
                continue
            files.add(path)
    return sorted(files)


def scan(
    files: list[Path],
    patterns: list[tuple[re.Pattern[str], str]],
) -> list[str]:
    """Walk ``files`` line by line and collect hit reports."""
    hits: list[str] = []
    for path in files:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError as exc:
            print(
                f"check_stale_refs.py: cannot read {path}: {exc}",
                file=sys.stderr,
            )
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(lines, start=1):
            if ALLOW_MARKER in line:
                continue
            for compiled, hint in patterns:
                if compiled.search(line):
                    snippet = line.strip()[:120]
                    hits.append(f"{rel}:{lineno}: {snippet}\n    → {hint}")
    return hits


def main() -> int:
    """Load the manifest, walk the include globs, print + return."""
    patterns = load_manifest()
    if not patterns:
        return 0

    files = discover_files()
    hits = scan(files, patterns)

    if hits:
        print("check_stale_refs.py: stale references found:", file=sys.stderr)
        for hit in hits:
            print(hit, file=sys.stderr)
        print(
            f"\n{len(hits)} stale reference(s) detected. Update docs to use "
            "the current names; if you need to keep one (e.g. CHANGELOG-style "
            "history) move it outside the scanned surfaces or add "
            f"`{ALLOW_MARKER}` to the offending line.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
