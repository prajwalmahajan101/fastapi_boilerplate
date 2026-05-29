"""OpenAPI / Swagger metadata — top-level description, tag docs, response refs.

Keeps the long-form Markdown out of ``src/app.py`` so the app factory stays
readable. Consumed by:

* :mod:`src.app` — passes :data:`API_DESCRIPTION` and :data:`TAGS_METADATA`
  to the ``FastAPI(...)`` constructor.
* route modules — pass the shared ``RESPONSES_*`` dicts (or the
  :data:`DEFAULT_RESPONSES` union) into each route's ``responses=`` kwarg so
  Swagger renders the documented error shape (always an
  :class:`ErrorEnvelope`).

``src.common`` is allowed to import from ``src.core`` — the dependency rule
only forbids the reverse direction.
"""

from __future__ import annotations

from typing import Any

from src.core.responses.envelope import ErrorEnvelope

API_DESCRIPTION = """\
FastAPI service boilerplate — a batteries-included starting point with a
standard response envelope, structured logging, a resilience layer
(circuit breaker / retry / cache / rate-limit), request auditing, and an
async SQLAlchemy data layer.

## Response envelope

Every response — success or error — uses a stable envelope:

```json
{
  "success": true,
  "message": "Human-readable summary.",
  "data": { ... },           // present on success, null on error
  "errors": null,            // null on success, list[ErrorDetail] on error
  "request_id": "abc-123"    // echoes X-Request-ID; auto-generated if absent
}
```

`ErrorDetail` carries `code`, `message`, and optional `field` / `details`.
Use `errors[*].code` for machine matching — the string is stable across
versions; the `message` is not.

## Error codes

| HTTP | `errors[*].code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR`        | Domain validation failed in service code. |
| 404 | `ENTITY_NOT_FOUND`        | Path target does not exist. |
| 422 | `VALIDATION_ERROR`        | Request body / path failed Pydantic validation. |
| 429 | `RATE_LIMITED`            | A rate-limit bucket tripped. |
| 500 | `INTERNAL_SERVER_ERROR`   | Unhandled server failure. |
| 502 | `EXTERNAL_TIMEOUT` / …    | A downstream/upstream dependency failed. |
| 503 | `SERVICE_UNAVAILABLE`     | A circuit breaker is open; retry after the window. |

## Pagination

List endpoints accept `page` (1-indexed, default `1`) and `size`
(default `20`). The response `data` is a `PaginatedData` block with
`list`, `current_page`, `size`, `total_count`, `total_pages`, `has_prev`,
and `has_next`.

## Request-ID

Set `X-Request-ID` to correlate your logs with ours. If omitted, the
server generates one and echoes it in the envelope and the `X-Request-ID`
response header.
"""

TAGS_METADATA: list[dict[str, Any]] = [
    {
        "name": "Health",
        "description": (
            "Liveness and readiness probes. No authentication required. "
            "`/healthz` checks process + DB liveness; `/readyz` additionally "
            "exercises Redis, the throttle backend, and the circuit-breaker "
            "registry. Both are mirrored under `/api/*` for ingress "
            "controllers that prefix-match."
        ),
    },
    {
        "name": "Example",
        "description": (
            "Example endpoints (hello + items CRUD) demonstrating the "
            "response envelope, the async service/repository layer, and "
            "rate-limit dependencies. Delete these once your own routes land."
        ),
    },
]


RESPONSES_BAD_REQUEST: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorEnvelope,
        "description": (
            "Domain validation rejected the request — a `ValidationError` "
            "raised by service code (e.g. a unique-key collision). "
            "`errors[0].code` is `VALIDATION_ERROR`. Distinct from 422 which "
            "is FastAPI's request-body shape check."
        ),
    },
}

RESPONSES_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorEnvelope,
        "description": (
            "The target entity does not exist. `errors[0].code` is `ENTITY_NOT_FOUND`."
        ),
    },
}

RESPONSES_VALIDATION: dict[int | str, dict[str, Any]] = {
    422: {
        "model": ErrorEnvelope,
        "description": (
            "Request body or path parameters failed Pydantic validation. "
            "`errors[*].field` points to the offending location."
        ),
    },
}

RESPONSES_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorEnvelope,
        "description": (
            "Missing or invalid API key. `errors[0].code` is "
            "`AUTHENTICATION_FAILED` (or `API_KEY_REVOKED` for a "
            "soft-revoked key)."
        ),
    },
}

RESPONSES_FORBIDDEN: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorEnvelope,
        "description": (
            "Authenticated principal does not hold the required "
            "`(resource, action)` permission. `errors[0].code` is "
            "`PERMISSION_DENIED`."
        ),
    },
}

RESPONSES_RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    429: {
        "model": ErrorEnvelope,
        "description": (
            "Rate limit exceeded for this endpoint or its burst window. "
            "`errors[0].code` is `RATE_LIMITED`."
        ),
    },
}

RESPONSES_BAD_GATEWAY: dict[int | str, dict[str, Any]] = {
    502: {
        "model": ErrorEnvelope,
        "description": (
            "A downstream dependency returned an unrecoverable error or the "
            "upstream HTTP call timed out. `errors[0].code` carries the "
            "family-specific value (e.g. `EXTERNAL_TIMEOUT`)."
        ),
    },
}

RESPONSES_SERVICE_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {
        "model": ErrorEnvelope,
        "description": (
            "An upstream circuit breaker is open — the protected dependency "
            "exceeded its failure threshold and calls are paused. Retry after "
            "the breaker's recovery window. `errors[0].code` is "
            "`SERVICE_UNAVAILABLE`."
        ),
    },
}

RESPONSES_INTERNAL_SERVER_ERROR: dict[int | str, dict[str, Any]] = {
    500: {
        "model": ErrorEnvelope,
        "description": (
            "Unhandled server failure — the request reached the app but an "
            "exception escaped the typed-error handlers. `errors[0].code` is "
            "`INTERNAL_SERVER_ERROR`."
        ),
    },
}


#: Sensible default set for a typical CRUD route. Spread it into a route's
#: ``responses=`` kwarg and add/override per route as needed::
#:
#:     @router.post("/items", responses={**DEFAULT_RESPONSES, **RESPONSES_NOT_FOUND})
DEFAULT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **RESPONSES_BAD_REQUEST,
    **RESPONSES_VALIDATION,
    **RESPONSES_RATE_LIMITED,
    **RESPONSES_INTERNAL_SERVER_ERROR,
}


__all__ = [
    "API_DESCRIPTION",
    "DEFAULT_RESPONSES",
    "RESPONSES_BAD_GATEWAY",
    "RESPONSES_BAD_REQUEST",
    "RESPONSES_FORBIDDEN",
    "RESPONSES_INTERNAL_SERVER_ERROR",
    "RESPONSES_NOT_FOUND",
    "RESPONSES_RATE_LIMITED",
    "RESPONSES_SERVICE_UNAVAILABLE",
    "RESPONSES_UNAUTHORIZED",
    "RESPONSES_VALIDATION",
    "TAGS_METADATA",
]
