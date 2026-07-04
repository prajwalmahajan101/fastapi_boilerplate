"""Unit tests for body-embedded PII redaction in api_log serialisation.

Exercises the resilience-kit value-scanning redactor wired into
``serialize_body`` / ``scrub_body_pii``: PII sitting *inside* a body value
(not just a sensitive header name) is masked before persistence.
"""

from __future__ import annotations

import pytest

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from src.core.api_log.inbound import _build_inbound_log
from src.core.api_log.sanitizers import UNSET, scrub_body_pii, serialize_body
from src.core.runtime import get_settings


def _request() -> Request:
    """Build a minimal POST ``Request`` for inbound-log construction."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "query_string": b"",
            "headers": [],
        }
    )


def test_scrub_masks_india_fintech_identifiers() -> None:
    """PAN / Aadhaar / IFSC / mobile embedded in a string are masked."""
    out = scrub_body_pii(
        "PAN ABCDE1234F, Aadhaar 2345 6789 0123, IFSC HDFC0001234, call 9876543210"
    )
    assert "ABCDE1234F" not in out
    assert "234567890123" not in out.replace(" ", "")
    assert "HDFC0001234" not in out
    assert "9876543210" not in out
    assert "[REDACTED]" in out


def test_scrub_masks_global_pii() -> None:
    """Email and Luhn-valid card numbers are masked by the global set."""
    out = scrub_body_pii("mail me a@b.com or card 4111 1111 1111 1111")
    assert "a@b.com" not in out
    assert "4111" not in out


def test_serialize_body_redacts_dict_values() -> None:
    """A PAN inside an innocuous dict field is masked in the serialised row."""
    out = serialize_body({"notes": "customer PAN ABCDE1234F", "amount": 500}, 10_000)
    assert out is not None
    assert "ABCDE1234F" not in out
    assert "[REDACTED]" in out
    # Non-PII values pass through unchanged.
    assert "500" in out


def test_non_pii_body_passes_through_unchanged() -> None:
    """A body with no PII is serialised verbatim (no false positives)."""
    assert scrub_body_pii("order 42 shipped to warehouse 7") == (
        "order 42 shipped to warehouse 7"
    )


def test_toggle_off_disables_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """With api_log_redact_body_pii off, PII is left intact (escape hatch)."""
    monkeypatch.setattr(get_settings(), "api_log_redact_body_pii", False)
    out = scrub_body_pii("PAN ABCDE1234F")
    assert out == "PAN ABCDE1234F"


def test_inbound_request_body_is_redacted() -> None:
    """The inbound builder scrubs PII in the raw request body (not just resp)."""
    log = _build_inbound_log(
        request=_request(),
        req_body_raw=b'{"description":"customer PAN ABCDE1234F"}',
        service_name="t",
        result=UNSET,
        duration_ms=1.0,
        exc_type=None,
        exc_msg=None,
    )
    assert log.request_body is not None
    assert "ABCDE1234F" not in log.request_body
    assert "[REDACTED]" in log.request_body


def test_inbound_response_body_is_redacted() -> None:
    """A rendered Response body has embedded PII scrubbed before persistence."""
    log = _build_inbound_log(
        request=_request(),
        req_body_raw=None,
        service_name="t",
        result=PlainTextResponse("contact a@b.com"),
        duration_ms=1.0,
        exc_type=None,
        exc_msg=None,
    )
    assert log.response_body is not None
    assert "a@b.com" not in log.response_body
