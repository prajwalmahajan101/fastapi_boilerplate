"""Unit tests for ``scripts/check_layering.py`` — ISSUE-029.

Three guarantees:

* a top-level ``from src.common…`` inside ``src/core/`` is caught.
* a *function-local* ``from src.common…`` is *also* caught — the
  AST walker descends into function bodies, so lazy imports no
  longer evade the check (the regression ISSUE-029 names).
* a clean file produces no violations.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_layering.py"
_spec = importlib.util.spec_from_file_location("check_layering", _SCRIPT)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_layering"] = checker
_spec.loader.exec_module(checker)  # type: ignore[arg-type]


def _violations(tmp_path: Path, body: str) -> list[tuple[int, str]]:
    """Run the module-level violation pass on a synthesised core file."""
    path = tmp_path / "core_under_test.py"
    path.write_text(body)
    return checker._violations(path)


def test_top_level_forbidden_import_is_caught(tmp_path: Path) -> None:
    body = "from src.common.enums import Foo\n"
    violations = _violations(tmp_path, body)
    assert [(1, "src.common.enums")] == violations


def test_function_local_forbidden_import_is_caught(tmp_path: Path) -> None:
    """Lazy imports inside closures must not evade the layering check."""
    body = (
        "def factory():\n"
        "    async def probe():\n"
        "        from src.repository.auth import APIKeyRepository\n"
        "        return None\n"
        "    return probe\n"
    )
    violations = _violations(tmp_path, body)
    assert violations == [(3, "src.repository.auth")]


def test_clean_core_file_has_no_violations(tmp_path: Path) -> None:
    body = (
        "from src.core.runtime import get_settings\n"
        "from src.core.utils.logging import get_logger\n"
        "\n"
        "def go():\n"
        "    return get_settings()\n"
    )
    assert _violations(tmp_path, body) == []
