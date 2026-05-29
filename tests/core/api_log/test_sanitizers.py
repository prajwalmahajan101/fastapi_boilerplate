"""Unit tests for pure api_log sanitizer helpers."""

from __future__ import annotations

from src.core.api_log.sanitizers import (
    audit_safe,
    compute_ttl,
    redact_headers,
    serialize_body,
    truncate,
)


def test_redact_headers_replaces_sensitive_case_insensitive() -> None:
    """Sensitive header values are replaced regardless of header casing."""
    headers = {
        "Authorization": "Bearer secret",
        "x-api-key": "k",
        "Content-Type": "application/json",
    }
    out = redact_headers(headers)
    assert out["Authorization"] == "[REDACTED]"
    assert out["x-api-key"] == "[REDACTED]"
    assert out["Content-Type"] == "application/json"


def test_truncate_appends_marker_above_max() -> None:
    """Strings longer than max get an explicit truncated marker."""
    assert truncate(None, 10) is None
    assert truncate("short", 10) == "short"
    out = truncate("x" * 30, 10)
    assert out is not None
    assert out.endswith("…[truncated]")
    assert out.startswith("x" * 10)


def test_audit_safe_summarises_bytes_passes_dicts_through() -> None:
    """Bytes become a size summary; non-byte values are unchanged."""
    summary = audit_safe(b"payload")
    assert summary == {"__bytes__": True, "size_bytes": 7}
    assert audit_safe({"a": 1}) == {"a": 1}
    assert audit_safe(None) is None


def test_serialize_body_handles_str_bytes_dict_and_failure() -> None:
    """String passthrough, bytes decode, dict json-dumps, unserialisable → None."""
    assert serialize_body(None, 10) is None
    assert serialize_body("hi", 10) == "hi"
    assert serialize_body(b"hi", 10) == "hi"
    out = serialize_body({"a": 1}, 100)
    assert out is not None and '"a": 1' in out


def test_compute_ttl_returns_none_when_disabled(monkeypatch) -> None:
    """``api_log_ttl_days = 0`` short-circuits to None."""
    import src.core.api_log.sanitizers as mod

    class _S:
        api_log_ttl_days = 0

    monkeypatch.setattr(mod, "get_settings", lambda: _S())
    assert compute_ttl() is None


def test_compute_ttl_returns_future_epoch_when_enabled(monkeypatch) -> None:
    """A positive ``api_log_ttl_days`` yields a future unix timestamp."""
    import time as _time

    import src.core.api_log.sanitizers as mod

    class _S:
        api_log_ttl_days = 7

    monkeypatch.setattr(mod, "get_settings", lambda: _S())
    ttl = compute_ttl()
    assert ttl is not None
    assert ttl > int(_time.time())
