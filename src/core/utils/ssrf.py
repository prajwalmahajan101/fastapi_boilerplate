"""SSRF defence + outbound allow-list.

Two layers, called in order by ``AsyncAPIClient.request``:

1. :func:`resolve_and_validate` resolves the URL's hostname, rejects
   non-http(s) schemes, and rejects any URL that resolves to a
   non-public address (RFC1918, loopback, link-local, multicast,
   reserved, unspecified). It returns the resolved IP set so the
   caller can **pin** those IPs across the validate → request
   boundary — pairing with :data:`pinned_dns` and a custom aiohttp
   resolver closes the classic DNS-rebinding TOCTOU where a malicious
   zone returns a public IP at validation time and a private one at
   request time.

2. :func:`assert_allowed_url` checks the URL host against
   ``CoreSettings.outbound_url_allowlist`` — a positive list (exact
   host or ``.suffix`` form) that blocks legitimate public hosts the
   service was never supposed to call. Empty list / ``"*"`` is
   permissive (default, matches today's behaviour).

The thin :func:`assert_public_url` shim keeps the historical entry
point so callers that don't need the pinned IP set are unchanged.

Disabled per-layer by ``CoreSettings.ssrf_block_private_ips=False``
(used by tests that hit localhost mock servers) and the allow-list
by leaving it empty.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from contextvars import ContextVar
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from src.core.exceptions.validation import ValidationError
from src.core.runtime import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

#: Per-task DNS pin. ``AsyncAPIClient.request`` populates this with
#: ``{host: {ip, ...}}`` right after :func:`resolve_and_validate` so the
#: custom aiohttp resolver returns *only* those IPs at dispatch time.
#: Empty / None means "no pin in effect" — the default async resolver
#: handles the lookup, and direct calls (e.g. from tests) work as
#: before.
pinned_dns: ContextVar[dict[str, set[str]] | None] = ContextVar(
    "pinned_dns", default=None
)


def safe_host(url: str) -> str:
    """Extract a logging-safe hostname (no port / path / query) from ``url``.

    Args:
        url: Any URL string.

    Returns:
        The hostname, ``"external service"`` if parsing fails so the
        log line still reads sensibly without leaking the full URL.
    """
    try:
        return urlparse(url).hostname or "external service"
    except Exception:
        return "external service"


def resolve_and_validate(url: str, *, strict: bool = True) -> set[str]:
    """Validate ``url`` and return the resolved IP set for pinning.

    ``strict=True`` (default; used by the HTTP-call path) rejects an
    unresolvable hostname. ``strict=False`` (used by save-time
    validators) accepts unresolvable hostnames so transient DNS
    failure does not block legitimate partner configuration; the
    HTTP-call path still gates the actual outbound request.

    Args:
        url: Outbound URL to validate.
        strict: When ``True`` an unresolvable hostname is an error.

    Returns:
        The set of resolved IPs (literal address when the host is an
        IP literal). Empty set when validation is disabled or the
        host did not resolve under ``strict=False``.

    Raises:
        ValidationError: URL scheme is not ``http``/``https``, no
            hostname, hostname cannot be resolved (when
            ``strict=True``), or resolves to a private/loopback/
            link-local/reserved/multicast/unspecified address.
    """
    if not get_settings().ssrf_block_private_ips:
        return set()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(
            f"URL scheme '{parsed.scheme}' is not allowed (only http/https).",
            details={"url": url, "scheme": parsed.scheme},
        )
    host = parsed.hostname
    if not host:
        raise ValidationError("URL has no hostname.", details={"url": url})

    try:
        literal = ipaddress.ip_address(host)
        addrs: set[str] = {str(literal)}
    except ValueError:
        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror as exc:
            if strict:
                raise ValidationError(
                    f"URL hostname '{host}' could not be resolved.",
                    details={"url": url, "host": host},
                ) from exc
            logger.info(
                "SSRF validator: %s did not resolve (strict=False, accepting).", host
            )
            return set()

    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            raise ValidationError(
                f"URL resolves to a non-public address ({addr}).",
                details={"url": url, "host": host, "address": addr},
            )
    return addrs


def assert_public_url(url: str, *, strict: bool = True) -> None:
    """Raise ``ValidationError`` if ``url`` resolves to a non-public address.

    Thin shim over :func:`resolve_and_validate` for callers that do
    not need the resolved IP set (e.g. save-time validators).

    Args:
        url: Outbound URL to validate.
        strict: When ``True`` an unresolvable hostname is an error.

    Raises:
        ValidationError: See :func:`resolve_and_validate`.
    """
    resolve_and_validate(url, strict=strict)


def assert_allowed_url(url: str) -> None:
    """Reject ``url`` when its host is not in ``outbound_url_allowlist``.

    Defence-in-depth alongside :func:`resolve_and_validate`. The SSRF
    guard blocks private IPs; the allow-list blocks legitimate public
    hosts the service was never supposed to call (data-exfiltration
    via a misconfigured partner URL, accidental request to a typo'd
    domain).

    Allow-list entries:

    * ``*`` — wildcard, allow anything. Use in local / dev.
    * ``example.com`` — exact host match.
    * ``.example.com`` — suffix match (any subdomain *and* the apex).

    Empty list = permissive (matches the historical behaviour). Prod
    and UAT should set the field explicitly per environment.

    Args:
        url: Outbound URL to validate.

    Raises:
        OutboundURLNotAllowedError: Host is not in the allow-list.
    """
    allow = list(getattr(get_settings(), "outbound_url_allowlist", []) or [])
    if not allow or "*" in allow:
        return

    from src.core.exceptions.infrastructure import (  # noqa: PLC0415
        OutboundURLNotAllowedError,
    )

    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise OutboundURLNotAllowedError("URL has no hostname.")

    for entry in (e.lower() for e in allow):
        if entry.startswith("."):
            if host == entry[1:] or host.endswith(entry):
                return
        elif host == entry:
            return
    raise OutboundURLNotAllowedError(
        f"Outbound URL host '{host}' is not in outbound_url_allowlist.",
    )
