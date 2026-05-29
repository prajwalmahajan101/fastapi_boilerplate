#!/usr/bin/env python3
"""Fail when any HTTP route is missing the documented OpenAPI metadata.

Every route under ``src/api/`` should declare:

* ``responses=`` containing at least the standard error envelope codes
  (``DEFAULT_RESPONSES`` = 400 / 422 / 429 / 500) so Swagger renders the
  documented error shape consistently across the whole surface; and
* ``response_model=`` so the typed success envelope is published in the
  OpenAPI schema instead of a free-form ``object`` for ``data``.

Adding a new route without either is a silent regression in the OpenAPI
contract.

This script AST-walks every ``.py`` file under ``src/api/`` and
inspects each ``@router.<method>(...)`` decorator for the spread
``**DEFAULT_RESPONSES`` (or the bare name) inside ``responses=`` and
for the presence of a ``response_model=`` kwarg. It does not boot the
app, so it stays fast enough for CI without requiring Postgres / Redis /
env wiring.

Exit codes:
    ``0`` — every route includes both required kwargs.
    ``1`` — one or more routes missing either; offending file:line printed.

Run manually via::

    python scripts/check_openapi_metadata.py

Wired as a local pre-commit hook in the ``manual`` stage so it runs on
demand without slowing per-commit feedback.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "src" / "api"

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
REQUIRED_NAME = "DEFAULT_RESPONSES"


def _is_route_decorator(node: ast.expr) -> bool:
    """Return True when *node* looks like ``@<router>.<verb>(...)``.

    Args:
        node: An AST decorator expression.

    Returns:
        True if the decorator is a router HTTP verb call.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ROUTE_METHODS
    )


def _responses_uses_default(call: ast.Call) -> bool | None:
    """Return whether the ``responses=`` kwarg includes ``DEFAULT_RESPONSES``.

    Args:
        call: The decorator call AST node.

    Returns:
        ``True`` when found, ``False`` when ``responses`` is set but does
        not mention ``DEFAULT_RESPONSES``, ``None`` when ``responses`` is
        absent (treated as a violation for non-trivial routes — the
        caller decides).
    """
    for kw in call.keywords:
        if kw.arg != "responses":
            continue
        # Look for **DEFAULT_RESPONSES (spread inside a dict literal) or the
        # bare name being passed.
        if isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if key is None:  # ** spread — Python represents as None key.
                    continue
            for value, key in zip(kw.value.values, kw.value.keys, strict=False):
                # Spread of a Name: the key is None and value is Name.
                if key is None and isinstance(value, ast.Name) and value.id == REQUIRED_NAME:
                    return True
                if key is None and isinstance(value, ast.Starred) and isinstance(value.value, ast.Name) and value.value.id == REQUIRED_NAME:  # pragma: no cover — older ast variants
                    return True
            # Fallback: walk all sub-nodes for the name.
            for sub in ast.walk(kw.value):
                if isinstance(sub, ast.Name) and sub.id == REQUIRED_NAME:
                    return True
            return False
        if isinstance(kw.value, ast.Name) and kw.value.id == REQUIRED_NAME:
            return True
        for sub in ast.walk(kw.value):
            if isinstance(sub, ast.Name) and sub.id == REQUIRED_NAME:
                return True
        return False
    return None


def _has_response_model(call: ast.Call) -> bool:
    """Return whether the decorator declares a ``response_model=`` kwarg.

    Args:
        call: The decorator call AST node.

    Returns:
        ``True`` when ``response_model=<expr>`` is present.
    """
    return any(kw.arg == "response_model" for kw in call.keywords)


def _violations(path: Path) -> list[tuple[int, str, str]]:
    """Find route decorators missing required OpenAPI kwargs in *path*.

    Args:
        path: Absolute path to a Python source file under ``src/api/``.

    Returns:
        List of ``(line_number, decorator_repr, missing_kwarg)`` tuples;
        empty when clean.
    """
    out: list[tuple[int, str, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not _is_route_decorator(deco):
                continue
            assert isinstance(deco, ast.Call)
            func = deco.func
            assert isinstance(func, ast.Attribute)
            verb = func.attr.upper()
            path_arg = ""
            if deco.args and isinstance(deco.args[0], ast.Constant):
                path_arg = str(deco.args[0].value)
            deco_repr = f"{verb} {path_arg}".strip()

            uses = _responses_uses_default(deco)
            if uses is False or uses is None:
                out.append((deco.lineno, deco_repr, "DEFAULT_RESPONSES"))
            if not _has_response_model(deco):
                out.append((deco.lineno, deco_repr, "response_model"))
    return out


def main() -> int:
    """Walk ``src/api/`` and report routes missing required OpenAPI kwargs.

    Returns:
        ``0`` when every route declares both ``DEFAULT_RESPONSES`` and
        ``response_model``; ``1`` otherwise.
    """
    failed = False
    for path in sorted(API_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        for lineno, deco, missing in _violations(path):
            rel = path.relative_to(ROOT)
            print(f"{rel}:{lineno}: route missing {missing}: {deco}")
            failed = True
    if failed:
        print(
            "\nEvery route under src/api/ must include both:\n"
            "  - DEFAULT_RESPONSES (400/422/429/500) in its responses= kwarg, "
            "so Swagger renders the ErrorEnvelope contract; and\n"
            "  - a response_model= kwarg (e.g. SuccessEnvelope[ItemRead]), "
            "so the typed success envelope is published in the OpenAPI schema.\n"
            "Health/probe routes that intentionally skip the baseline should "
            "still pass the spread and rely on FastAPI's default override "
            "behaviour.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
