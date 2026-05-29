"""Unit tests for ``build_error_message``."""

from __future__ import annotations

from src.core.api_log.error_messages import build_error_message
from src.core.exceptions.api import APIError


def test_build_error_message_for_plain_exception_returns_str() -> None:
    """A plain Exception folds down to its ``str()`` representation."""
    assert build_error_message(ValueError("boom")) == "boom"


def test_build_error_message_for_api_error_includes_all_fields() -> None:
    """All set APIError fields appear pipe-delimited in order."""
    exc = APIError(
        "upstream rejected",
        status_code=502,
        response_body='{"err": "x"}',
        details={"url": "/x"},
    )
    msg = build_error_message(exc)
    assert msg.startswith("upstream rejected")
    assert "status_code=502" in msg
    assert "response_body=" in msg
    assert "details=" in msg
    # Pipe-delimited.
    assert msg.count(" | ") >= 3


def test_build_error_message_omits_empty_api_error_fields() -> None:
    """Fields left to defaults (None / empty) are not emitted."""
    exc = APIError("partial")
    msg = build_error_message(exc)
    # status_code defaults to 502 (class-level), so it should appear.
    assert "status_code=502" in msg
    # response_body is None → must NOT appear.
    assert "response_body=" not in msg
    # details defaults to {} → falsy → must NOT appear.
    assert "details=" not in msg
