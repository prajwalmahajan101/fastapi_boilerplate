"""Unit tests for ``scripts/check_openapi_metadata.py`` — model_dump pass.

ISSUE-028 added a third check on top of the two pre-existing rules
(``DEFAULT_RESPONSES`` and ``response_model``): a route declaring
``response_model=`` must not chain ``.model_validate(...).model_dump()``
on its return value, because ``SuccessResponse`` already serialises
the envelope through pydantic in one pass.

Input-side ``payload.model_dump(...)`` (converting a request body to
a service-layer kwargs dict) is **not** the anti-pattern and must
not be flagged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_openapi_metadata.py"
_spec = importlib.util.spec_from_file_location("check_openapi_metadata", _SCRIPT)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_openapi_metadata"] = checker
_spec.loader.exec_module(checker)  # type: ignore[arg-type]


def _write(tmp_path: Path, body: str) -> Path:
    """Write ``body`` to a fake route file under ``src/api/v1/foo.py``."""
    path = tmp_path / "foo.py"
    path.write_text(body)
    return path


def test_round_trip_chain_is_flagged(tmp_path: Path) -> None:
    body = (
        "from src.common.openapi_metadata import DEFAULT_RESPONSES\n"
        "from src.core.responses import SuccessResponse, SuccessEnvelope\n"
        "\n"
        "@router.get(\n"
        "    '/x',\n"
        "    response_model=SuccessEnvelope[int],\n"
        "    responses={**DEFAULT_RESPONSES},\n"
        ")\n"
        "async def handler():\n"
        "    return SuccessResponse(data=Foo.model_validate(x).model_dump())\n"
    )
    path = _write(tmp_path, body)
    violations = checker._violations(path)
    kinds = sorted({k for _, _, k in violations})
    assert "no-model_dump-round-trip" in kinds


def test_input_side_model_dump_is_not_flagged(tmp_path: Path) -> None:
    """``payload.model_dump()`` feeding the service layer is legitimate."""
    body = (
        "from src.common.openapi_metadata import DEFAULT_RESPONSES\n"
        "from src.core.responses import SuccessResponse, SuccessEnvelope\n"
        "\n"
        "@router.post(\n"
        "    '/x',\n"
        "    response_model=SuccessEnvelope[int],\n"
        "    responses={**DEFAULT_RESPONSES},\n"
        ")\n"
        "async def handler(payload):\n"
        "    args = payload.model_dump(exclude_unset=True)\n"
        "    return SuccessResponse(data=Foo.model_validate(x))\n"
    )
    path = _write(tmp_path, body)
    violations = checker._violations(path)
    kinds = sorted({k for _, _, k in violations})
    assert "no-model_dump-round-trip" not in kinds


def test_clean_handler_has_no_violations(tmp_path: Path) -> None:
    body = (
        "from src.common.openapi_metadata import DEFAULT_RESPONSES\n"
        "from src.core.responses import SuccessResponse, SuccessEnvelope\n"
        "\n"
        "@router.get(\n"
        "    '/x',\n"
        "    response_model=SuccessEnvelope[int],\n"
        "    responses={**DEFAULT_RESPONSES},\n"
        ")\n"
        "async def handler():\n"
        "    return SuccessResponse(data=Foo.model_validate(x))\n"
    )
    path = _write(tmp_path, body)
    violations = checker._violations(path)
    assert violations == []
