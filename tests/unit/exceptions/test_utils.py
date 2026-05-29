"""Unit tests for ``exception_response_payload`` / ``exception_wire_status``."""

from __future__ import annotations

from src.core.exceptions.utils import (
    exception_response_payload,
    exception_wire_status,
)


class _ExcWithResponse(Exception):
    def __init__(self, response: dict) -> None:
        super().__init__("x")
        self.response = response


class _ExcWithBody(Exception):
    def __init__(self, body: str) -> None:
        super().__init__("x")
        self.response_body = body


class _ExcWithDetails(Exception):
    def __init__(self, details: dict) -> None:
        super().__init__("x")
        self.details = details


class _ExcWithWireStatus(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("x")
        self.response_status_code = code


class _ExcWithStatusCode(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("x")
        self.status_code = code


def test_response_payload_prefers_response_dict() -> None:
    """``.response`` dict beats every other source."""
    exc = _ExcWithResponse({"err": "rate_limit"})
    assert exception_response_payload(exc) == {"err": "rate_limit"}


def test_response_payload_parses_response_body_json() -> None:
    """JSON-string ``.response_body`` parses to a dict."""
    exc = _ExcWithBody('{"err": "bad_input"}')
    assert exception_response_payload(exc) == {"err": "bad_input"}


def test_response_payload_falls_back_to_details() -> None:
    """``.details`` dict is the last-resort source."""
    exc = _ExcWithDetails({"hint": "retry"})
    assert exception_response_payload(exc) == {"hint": "retry"}


def test_response_payload_returns_none_when_unrecoverable() -> None:
    """Plain exceptions yield ``None``."""
    assert exception_response_payload(RuntimeError("opaque")) is None


def test_wire_status_prefers_response_status_code() -> None:
    """``.response_status_code`` wins over ``.status_code``."""
    exc = _ExcWithWireStatus(429)
    exc.status_code = 502  # type: ignore[attr-defined]
    assert exception_wire_status(exc) == 429


def test_wire_status_uses_status_code_when_only_one() -> None:
    """``.status_code`` is picked when nothing higher-priority exists."""
    assert exception_wire_status(_ExcWithStatusCode(503)) == 503


def test_wire_status_defaults_to_502() -> None:
    """Plain exceptions default to 502 Bad Gateway."""
    assert exception_wire_status(RuntimeError("oops")) == 502
