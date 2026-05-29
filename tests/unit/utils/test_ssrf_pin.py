"""SSRF DNS pin + outbound allow-list."""

from __future__ import annotations

import pytest

from src.core.exceptions.infrastructure import OutboundURLNotAllowedError
from src.core.exceptions.validation import ValidationError
from src.core.utils import ssrf


class _StubSettings:
    ssrf_block_private_ips: bool = True
    outbound_url_allowlist: list[str] = []


@pytest.fixture(autouse=True)
def _reset_pin():
    """Each test starts with an empty pin contextvar."""
    token = ssrf.pinned_dns.set(None)
    yield
    ssrf.pinned_dns.reset(token)


def _patch_settings(monkeypatch, **overrides):
    s = _StubSettings()
    for k, v in overrides.items():
        setattr(s, k, v)
    monkeypatch.setattr(ssrf, "get_settings", lambda: s)


def test_resolve_and_validate_returns_ip_set_for_literal(monkeypatch):
    _patch_settings(monkeypatch)
    ips = ssrf.resolve_and_validate("https://8.8.8.8/")
    assert ips == {"8.8.8.8"}


def test_resolve_and_validate_rejects_private_literal(monkeypatch):
    _patch_settings(monkeypatch)
    with pytest.raises(ValidationError):
        ssrf.resolve_and_validate("https://10.0.0.1/")


def test_resolve_and_validate_pinning_roundtrip(monkeypatch):
    _patch_settings(monkeypatch)
    # Pretend getaddrinfo returned this public IP at validation time.
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda h, p: [(2, 1, 6, "", ("142.250.4.100", 0))],
    )
    ips = ssrf.resolve_and_validate("https://example.com/x")
    assert ips == {"142.250.4.100"}
    # Caller would now pin: dispatch must see exactly the same IP.
    ssrf.pinned_dns.set({"example.com": ips})
    assert ssrf.pinned_dns.get() == {"example.com": {"142.250.4.100"}}


def test_allow_list_empty_is_permissive(monkeypatch):
    _patch_settings(monkeypatch, outbound_url_allowlist=[])
    ssrf.assert_allowed_url("https://anywhere.example/")


def test_allow_list_wildcard_is_permissive(monkeypatch):
    _patch_settings(monkeypatch, outbound_url_allowlist=["*"])
    ssrf.assert_allowed_url("https://anywhere.example/")


def test_allow_list_exact_match(monkeypatch):
    _patch_settings(monkeypatch, outbound_url_allowlist=["example.com"])
    ssrf.assert_allowed_url("https://example.com/path")
    with pytest.raises(OutboundURLNotAllowedError):
        ssrf.assert_allowed_url("https://sub.example.com/")
    with pytest.raises(OutboundURLNotAllowedError):
        ssrf.assert_allowed_url("https://other.example/")


def test_allow_list_suffix_match(monkeypatch):
    _patch_settings(monkeypatch, outbound_url_allowlist=[".example.com"])
    ssrf.assert_allowed_url("https://example.com/path")  # apex
    ssrf.assert_allowed_url("https://api.example.com/x")  # subdomain
    with pytest.raises(OutboundURLNotAllowedError):
        ssrf.assert_allowed_url("https://attacker.com/")
