"""Generic helpers for normalizing outbound-call exceptions.

Two helpers used by outbound HTTP clients and the audit layer to record
a consistent shape regardless of which exception family surfaced the
failure — the transport-layer ``APIError`` (carrying ``status_code`` /
``response_body``), or any project-specific subclass that wraps a
2xx-with-error envelope under ``response`` / ``response_status_code``.

Lives under :mod:`src.core.exceptions` so any outbound module can reuse
the normalization — duck-typed via :func:`getattr`, so no exception
subclass needs to import this file.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import status


def exception_response_payload(exc: BaseException) -> dict[str, Any] | None:
    """Return the upstream response body as a dict, regardless of exception type.

    * Exceptions that already expose ``response`` as a dict (e.g.
      project-specific errors raised on 2xx-with-error payloads) — that
      dict wins.
    * Exceptions that expose ``response_body`` as a raw JSON string
      (the transport-layer ``APIError`` family) — parse it; on parse
      failure fall back to ``details`` so the audit row still records
      something useful.

    Args:
        exc: The exception raised by the outbound client / transport layer.

    Returns:
        The upstream response body as a dict, or ``None`` when nothing
        usable can be extracted.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response
    body = getattr(exc, "response_body", None)
    if isinstance(body, str) and body:
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        return details
    return None


def exception_wire_status(exc: BaseException) -> int:
    """Resolve the upstream wire status from any outbound-call exception.

    Preference order:
        1. ``response_status_code`` — set by project-specific errors
           that wrap a raw upstream HTTP status alongside a 2xx-shaped
           envelope.
        2. ``status_code`` — set by the transport-layer ``APIError``
           family on non-2xx responses.
        3. Generic ``HTTP_502_BAD_GATEWAY`` — fallback so the audit row
           always carries a number.

    Args:
        exc: The exception raised by the outbound client / transport layer.

    Returns:
        Best-effort HTTP status to record on the audit row.
    """
    return (
        getattr(exc, "response_status_code", None)
        or getattr(exc, "status_code", None)
        or status.HTTP_502_BAD_GATEWAY
    )


__all__ = ["exception_response_payload", "exception_wire_status"]
