"""Pure sanitizers / serializers for ``api_logs`` payloads.

The audit-log decorators feed raw request/response material into these
helpers before persistence — header redaction, body truncation, JSONB
safety for ``extra`` kwargs, settings-derived TTL. The helpers are
stateless and synchronous so they can be unit-tested without any of the
decorator machinery.

Originally lived inside ``api_log.decorators`` alongside the public
decorators; extracted so the module purpose matches its name (audit
decorators, not sanitizers + decorators + dispatch + serializers in one
file). See plan P9 in the Code Structure & Organization audit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.runtime import get_settings

_UNSET: Any = object()


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values replaced by ``[REDACTED]``.

    Header names are matched case-insensitively against
    ``api_log_sensitive_headers`` (Authorization, X-API-Key, Cookie, …).

    Args:
        headers: Raw request or response headers.

    Returns:
        Sanitised copy safe to persist in the audit log.
    """
    sensitive = {h.lower() for h in get_settings().api_log_sensitive_headers}
    return {
        k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in headers.items()
    }


def truncate(text: str | None, max_len: int) -> str | None:
    """Cap ``text`` at ``max_len`` chars, appending an ellipsis marker.

    Keeps the audit log column from blowing past its width limit while
    leaving an obvious "this was truncated" marker for diagnostics.

    Args:
        text: Source string (may be ``None``).
        max_len: Maximum retained length before truncation.

    Returns:
        ``None`` when input is ``None``; otherwise the original string
        or a truncated copy ending in ``"…[truncated]"``.
    """
    if text is None:
        return None
    return text if len(text) <= max_len else text[:max_len] + "…[truncated]"


def audit_safe(value: Any) -> Any:
    """Render ``value`` in a JSONB-safe shape for the ``extra`` column.

    Raw bytes (e.g. ``file_bytes`` from a multipart upload) cannot land
    in JSONB — passing them through caused the persist coroutine to
    raise, which the fire-and-forget queue then swallowed, so document
    uploads silently produced no ``api_logs`` row. Convert bytes to a
    size summary and let everything else fall through; the persist
    layer handles unexpected types with ``json.dumps(default=str)``.

    Args:
        value: A kwarg value captured for the ``extra`` JSON column.

    Returns:
        A JSON-safe replacement for ``bytes`` / ``bytearray``; ``value``
        unchanged otherwise.
    """
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": True, "size_bytes": len(value)}
    return value


def serialize_body(value: Any, max_len: int) -> str | None:
    """Render ``value`` as a string body of at most ``max_len`` chars.

    Strings pass through; bytes are UTF-8-decoded with errors replaced;
    everything else is JSON-dumped with ``default=str`` so the call
    never raises on unexpected payload shapes.

    Args:
        value: Body payload (str, bytes, dict, list, model, etc.).
        max_len: Maximum length passed to :func:`truncate`.

    Returns:
        Truncated string, ``None`` when input is ``None`` / ``_UNSET``,
        or ``None`` on a serialization failure (logged-then-swallowed).
    """
    if value is None or value is _UNSET:
        return None
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = json.dumps(value, default=str)
        return truncate(text, max_len)
    except Exception:  # noqa: BLE001 — audit-only sink: serialization failure on caller-supplied payload must never raise into the request path; drop to None.
        return None


def compute_ttl() -> int | None:
    """Return a unix-epoch expiry derived from ``api_log_ttl_days``.

    ``ttl_expires_at`` is consumed by a downstream pruning job; ``None``
    means "no expiry / keep forever".

    Returns:
        Unix timestamp ``ttl_days`` from now, or ``None`` when the
        setting is ``0`` / unset.
    """
    days = get_settings().api_log_ttl_days
    if not days:
        return None
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


__all__ = [
    "_UNSET",
    "audit_safe",
    "compute_ttl",
    "redact_headers",
    "serialize_body",
    "truncate",
]
