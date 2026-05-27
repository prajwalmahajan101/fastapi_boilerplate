"""SSRF defense — reject URLs that resolve to non-public IP space.

Called by :func:`src.core.utils.http_client.AsyncAPIClient._request` before
each outbound HTTP call. The check is best-effort (DNS may resolve
differently at request time vs. validation time, and a malicious resolver
can change answers), but it stops the most common variants — RFC1918,
loopback, link-local, multicast, and non-http(s) schemes.

Disabled by setting ``CoreSettings.ssrf_block_private_ips = False`` (use
in tests that hit localhost mock servers).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from src.core.exceptions.validation import ValidationError
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)


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


def assert_public_url(url: str, *, strict: bool = True) -> None:
    """Raise ``ValidationError`` if ``url`` resolves to a non-public address.

    ``strict=True`` (default; used by the HTTP-call path) rejects an
    unresolvable hostname. ``strict=False`` (used by save-time
    validators) accepts unresolvable hostnames so transient DNS
    failure does not block legitimate partner configuration; the
    HTTP-call path still gates the actual outbound request.

    Args:
        url: Outbound URL to validate.
        strict: When ``True`` an unresolvable hostname is an error.

    Raises:
        ValidationError: URL scheme is not ``http``/``https``, no
            hostname, hostname cannot be resolved (when
            ``strict=True``), or resolves to a private/loopback/
            link-local/reserved/multicast/unspecified address.
    """
    if not get_settings().ssrf_block_private_ips:
        return

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
        addrs = {str(literal)}
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
            return

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
