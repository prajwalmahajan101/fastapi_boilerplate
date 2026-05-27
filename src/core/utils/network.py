"""Request-side networking helpers shared across middleware and rate-limiting.

The two original consumers (``RequestLoggingMiddleware`` and the throttle
scope objects) each carried a private ``_client_ip`` copy with identical
behaviour. Extracting it here makes the proxy-header policy a single
source of truth — when ``trust_proxy_headers`` flips on the same logic
fires for audit logs and rate limits, so the two systems can never
disagree on who the caller is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.runtime import get_settings

if TYPE_CHECKING:
    from starlette.requests import Request


def client_ip(request: "Request") -> str:
    """Resolve the request's client IP, honouring ``trust_proxy_headers``.

    When ``trust_proxy_headers`` is enabled the helper trusts
    ``X-Forwarded-For`` (taking the first hop) and falls back to
    ``X-Real-IP``. Otherwise it returns the direct socket peer. The
    string ``"unknown"`` is returned when no peer is recorded — the
    callers (rate limiter, audit log) treat it as a valid bucket label.

    Args:
        request: Incoming Starlette / FastAPI request.

    Returns:
        Best-effort client IP string. Never raises.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"


__all__ = ["client_ip"]
