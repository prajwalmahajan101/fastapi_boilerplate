"""Guard against in-tree imports of modules marked Dormant.

Dormant modules ship for downstream forks but are not wired into any
request path today (see ``docs/INDEX.md`` § "Dormant modules"). They
carry a ``Dormant:`` callout in their module docstring naming the
precondition for re-activation.

This test fails if any file under ``src/`` imports one of the listed
dormant modules. The intent is to keep the dormant set honest: the
moment a feature actually wires one of these helpers in, the offender
must land alongside a matching integration test, and the dormant entry
moves out of this list and out of ``docs/INDEX.md``.

Mirror the style of ``test_check_layering.py`` and
``test_no_inline_auth_imports.py`` — a directory walk over ``src/``,
``ast.parse`` per file, no string-grep heuristics.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT: Path = Path(__file__).resolve().parents[3] / "src"

# Dotted module paths that callers under src/ must not import. Keep in
# sync with docs/INDEX.md § "Dormant modules" and the Dormant callouts
# in each module's docstring.
_DORMANT_MODULES: frozenset[str] = frozenset(
    {
        "src.core.utils.s3",
        "src.core.utils.ses",
        "src.core.utils.function_logger",
        "src.core.api_log.outbound",
        "src.management.run_worker",
    }
)

# Re-export shims: their whole job is to expose the dormant symbol on a
# stable public path. Allow-listed because they are not "call sites" —
# nothing here actually invokes the dormant code; it only re-publishes
# names. The dormant gate cares about request-path *callers*. If you
# add a new shim, list it here with the reason.
_REEXPORT_SHIMS: frozenset[Path] = frozenset(
    {
        _SRC_ROOT / "core" / "__init__.py",
        _SRC_ROOT / "core" / "api_log" / "decorators.py",
    }
)


def _module_dotted_name(path: Path) -> str:
    """Return the dotted module name for ``path`` (relative to repo root)."""
    repo_root = _SRC_ROOT.parent
    rel = path.relative_to(repo_root).with_suffix("")
    return ".".join(rel.parts)


def _is_dormant_target(module: str | None) -> bool:
    """True if ``module`` (or any prefix of it) is a dormant target."""
    if not module:
        return False
    if module in _DORMANT_MODULES:
        return True
    # Catch sub-attribute imports like ``from src.core.utils.s3 import X``
    # where node.module is exactly ``src.core.utils.s3`` (covered above)
    # *and* ``import src.core.utils.s3`` style.
    return any(module == d or module.startswith(d + ".") for d in _DORMANT_MODULES)


def _offenders(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, dotted_name)`` for every dormant import in ``path``."""
    tree = ast.parse(path.read_text())
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_dormant_target(node.module):
                hits.append((node.lineno, node.module or ""))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_dormant_target(alias.name):
                    hits.append((node.lineno, alias.name))
    return hits


def _iter_src_files() -> list[Path]:
    """All ``*.py`` files under ``src/`` except dormant modules themselves."""
    files: list[Path] = []
    dormant_paths = {
        _SRC_ROOT.parent / Path(*d.split(".")).with_suffix(".py")
        for d in _DORMANT_MODULES
    }
    for path in _SRC_ROOT.rglob("*.py"):
        if path in dormant_paths or path in _REEXPORT_SHIMS:
            continue
        files.append(path)
    return sorted(files)


@pytest.mark.parametrize(
    "path", _iter_src_files(), ids=lambda p: str(p.relative_to(_SRC_ROOT.parent))
)
def test_no_dormant_imports(path: Path) -> None:
    hits = _offenders(path)
    assert hits == [], (
        f"{path.relative_to(_SRC_ROOT.parent)} imports a dormant module "
        f"at lines {[lineno for lineno, _ in hits]}: "
        f"{sorted({name for _, name in hits})}. Either remove the import, "
        f"or move the module out of the dormant list in "
        f"tests/unit/scripts/test_no_dormant_imports.py + docs/INDEX.md "
        f"and land a matching integration test in the same change."
    )
