"""Payload sanitisers consumed by :class:`AsyncAPIClient` and friends.

Two pure helpers that the HTTP client publishes onto the
``outbound_response_meta_ctx`` so the audit decorator can persist a
JSONB-safe shape:

* :func:`summarise_body_for_audit` — handle ``aiohttp.FormData`` and raw
  bytes that the audit JSONB column cannot accept verbatim.
* :func:`serialize_error_body` — best-effort JSON encode an upstream
  error body for ``APIError.response_body``.

Lives next to the audit decorators rather than inside the
``resilience_kit.http_client`` module so the kit's HTTP client stays
"just the client" and the audit-shape concerns stay in this repo.
"""

from __future__ import annotations

import json as _json
from typing import Any

try:
    import aiohttp
except (
    ImportError
):  # aiohttp is optional for callers that only use serialize_error_body
    aiohttp = None  # type: ignore[assignment]


def summarise_body_for_audit(value: Any) -> Any:
    """Return a JSON-safe representation of an outbound request body.

    The audit decorator persists ``request_body_data`` into a JSONB
    column. Multipart payloads (``aiohttp.FormData``) and raw ``bytes``
    are not JSON-serialisable, so passing them through verbatim caused
    the audit row to be silently dropped by the fire-and-forget queue —
    which is exactly why document uploads were missing from ``api_logs``.

    For ``FormData`` we record the field names plus any file parts'
    filename / content type / byte size. For ``bytes`` we record the
    length. Anything else (dicts, ``None``) is returned unchanged.

    Args:
        value: Whatever was passed as ``data=`` to the underlying
            HTTP request.

    Returns:
        A JSON-safe object that mirrors the wire payload's shape without
        the binary bytes, or the original value when already safe.
    """
    if value is None:
        return None
    if aiohttp is not None and isinstance(value, aiohttp.FormData):
        try:
            fields: list[dict[str, Any]] = []
            for field in getattr(value, "_fields", []) or []:
                opts, headers, body = field
                entry: dict[str, Any] = {"name": opts.get("name")}
                if "filename" in opts:
                    entry["filename"] = opts["filename"]
                ctype = headers.get("Content-Type") if headers else None
                if ctype:
                    entry["content_type"] = ctype
                if isinstance(body, (bytes, bytearray)):
                    entry["size_bytes"] = len(body)
                elif isinstance(body, str):
                    entry["value"] = body if len(body) <= 200 else body[:200] + "…"
                fields.append(entry)
            return {"__multipart__": True, "fields": fields}
        except Exception:  # noqa: BLE001 — audit-only fallback: any failure introspecting FormData internals (private attr, aiohttp version drift) degrades to the bytes-length / passthrough path; never abort the call.
            pass
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": True, "size_bytes": len(value)}
    return value


def serialize_error_body(body: Any) -> str | None:
    """Best-effort JSON-encode an upstream error body for ``APIError.response_body``.

    Falls back to ``str(body)`` if the value isn't JSON-serialisable so
    a malformed partner response never masks the real failure under a
    secondary ``TypeError``.

    Args:
        body: Parsed response body (dict, list, str, or anything).

    Returns:
        JSON string when possible, the original string when ``body`` is
        already a string, ``str(body)`` as a last resort, or ``None``
        when no body was captured.
    """
    if body is None:
        return None
    if isinstance(body, str):
        return body
    try:
        return _json.dumps(body, default=str)
    except Exception:  # noqa: BLE001 — last-resort encode: a non-JSON-able partner body must still surface as a string in the audit row rather than crash the error-mapping path.
        return str(body)


__all__ = ["serialize_error_body", "summarise_body_for_audit"]
