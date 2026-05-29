"""Guard against the function-local ``from src.auth.jwt import …`` pattern.

The pattern grew naturally because ``src/api/v1/auth.py`` is imported
unconditionally by ``v1/__init__.py`` but the JWT routes inside it
should not force PyJWT into the import graph for deployments that
disable the provider. The right answer (commit 6 / ISSUE-027) is to
keep JWT routes in their own module ``src/api/v1/auth_jwt.py`` that
``v1/__init__.py`` imports conditionally — *that* module can use
module-top imports because it is only loaded when JWT is enabled.

This test fails if anyone reintroduces a function-local
``from src.auth.jwt import …`` into the unconditionally-loaded files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Files that are imported even when JWT is disabled. Any function-local
# `from src.auth.jwt import …` here re-creates the smell ISSUE-027 closed.
_PROTECTED_FILES: tuple[Path, ...] = (
    Path(__file__).resolve().parents[3] / "src" / "api" / "v1" / "auth.py",
    Path(__file__).resolve().parents[3] / "src" / "api" / "v1" / "__init__.py",
)


def _function_local_jwt_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, dotted_name)`` for every offending import."""
    tree = ast.parse(path.read_text())
    offenders: list[tuple[int, str]] = []

    class _Walker(ast.NodeVisitor):
        def _scan(self, body: list[ast.stmt]) -> None:
            for node in ast.walk(ast.Module(body=body, type_ignores=[])):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("src.auth.jwt"):
                        offenders.append((node.lineno, node.module))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._scan(node.body)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._scan(node.body)
            self.generic_visit(node)

    _Walker().visit(tree)
    return offenders


@pytest.mark.parametrize("path", _PROTECTED_FILES, ids=lambda p: p.name)
def test_no_function_local_jwt_imports(path: Path) -> None:
    offenders = _function_local_jwt_imports(path)
    assert offenders == [], (
        f"{path.name} has function-local `from src.auth.jwt import …` "
        f"at lines {[lineno for lineno, _ in offenders]} — move the route "
        f"into src/api/v1/auth_jwt.py (loaded conditionally) instead."
    )
