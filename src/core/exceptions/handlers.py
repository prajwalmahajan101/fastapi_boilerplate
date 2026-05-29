"""FastAPI exception handlers + a thread-safe status-code registry.

The handlers rewrap every error into the standard envelope::

    {"success": false, "message": str, "data": null,
     "errors": [...], "request_id": str | null}

``register_exception_mapping`` lets domain code attach an HTTP status to a
custom exception class without touching the handler itself. Mappings are
checked with ``isinstance`` in registration order — register specific
subclasses before their parents.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.base.exception import BaseCustomError
from src.core.context import get_request_id
from src.core.exceptions.auth import (
    APIKeyRevokedError,
    AuthenticationFailedError,
    PermissionDeniedError,
)
from src.core.exceptions.infrastructure import (
    DecryptionError,
    ExternalServiceError,
    ExternalTimeoutError,
    InfrastructureError,
    S3Error,
    SESError,
    ServiceUnavailableError,
    UpstreamPushError,
)
from src.core.exceptions.rate_limit import RateLimitError
from src.core.exceptions.repository import EntityNotFoundError, RepositoryError
from src.core.exceptions.validation import ValidationError
from src.core.responses.envelope import ErrorResponse

logger = logging.getLogger(__name__)

_status_map_builder: list[tuple[type[BaseCustomError], int]] = []
_status_map_cache: tuple[tuple[type[BaseCustomError], int], ...] | None = None
_status_map_lock = Lock()


def register_exception_mapping(
    exc_class: type[BaseCustomError],
    status_code: int,
) -> None:
    """Bind *exc_class* to *status_code*. Last-registered, first-evaluated.

    Args:
        exc_class: A ``BaseCustomError`` subclass.
        status_code: HTTP status to return for matches.
    """
    global _status_map_cache
    with _status_map_lock:
        _status_map_builder.append((exc_class, status_code))
        _status_map_cache = None


def _get_status_map() -> tuple[tuple[type[BaseCustomError], int], ...]:
    global _status_map_cache
    cached = _status_map_cache
    if cached is not None:
        return cached
    with _status_map_lock:
        if _status_map_cache is None:
            _status_map_cache = tuple(_status_map_builder)
        return _status_map_cache


async def custom_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle every ``BaseCustomError`` subclass via the status map.

    Args:
        request: The incoming FastAPI request.
        exc: The raised exception (must be a ``BaseCustomError``).

    Returns:
        JSON envelope with the mapped status code.
    """
    assert isinstance(exc, BaseCustomError)
    status_code = exc.status_code or status.HTTP_500_INTERNAL_SERVER_ERROR
    for cls, code in _get_status_map():
        if isinstance(exc, cls):
            status_code = code
            break

    headers = exc.response_headers() if isinstance(exc, RateLimitError) else None
    return ErrorResponse(
        message=exc.message,
        errors=[exc.to_error_dict()],
        status_code=status_code,
        headers=headers,
        request_id=exc.request_id or get_request_id(),
    )


async def request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """Wrap FastAPI's 422 request-validation error in the standard envelope.

    Args:
        request: The incoming FastAPI request.
        exc: The ``RequestValidationError`` raised by FastAPI.

    Returns:
        JSON envelope with HTTP 422 and per-field errors.
    """
    assert isinstance(exc, RequestValidationError)
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []))
        errors.append(
            {
                "code": "VALIDATION_ERROR",
                "message": err.get("msg", "Invalid input."),
                "field": loc or None,
                "details": {"type": err.get("type")},
            }
        )
    return ErrorResponse(
        message="Validation failed.",
        errors=errors,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort 500 response for anything not otherwise mapped.

    Args:
        request: The incoming FastAPI request.
        exc: The unhandled exception (logged with traceback).

    Returns:
        JSON envelope with HTTP 500 and a generic message.
    """
    logger.exception("Unhandled exception in %s %s", request.method, request.url.path)
    return ErrorResponse(
        message="An unexpected error occurred.",
        errors=[
            {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred.",
                "field": None,
                "details": None,
            }
        ],
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire all three handlers onto the FastAPI application instance.

    Args:
        app: The FastAPI app to register handlers on.
    """
    app.add_exception_handler(BaseCustomError, custom_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


# ── Pre-registered class → status mappings (specific → general) ──────────────
# Order matters: the handler returns on the first ``isinstance`` match, so
# every concrete subclass must be listed *before* its ancestor parent. New
# project-level subclasses inherit the right status from the parent
# registrations below — no per-subclass entry is needed unless the subclass
# overrides the family's status code. Register project-specific families
# (auth, a third-party integration, etc.) from your own code by calling
# ``register_exception_mapping`` — typically once at app startup.
register_exception_mapping(EntityNotFoundError, status.HTTP_404_NOT_FOUND)
register_exception_mapping(ValidationError, status.HTTP_400_BAD_REQUEST)
register_exception_mapping(ServiceUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE)
register_exception_mapping(ExternalTimeoutError, status.HTTP_502_BAD_GATEWAY)
register_exception_mapping(S3Error, status.HTTP_502_BAD_GATEWAY)
register_exception_mapping(SESError, status.HTTP_502_BAD_GATEWAY)
register_exception_mapping(UpstreamPushError, status.HTTP_502_BAD_GATEWAY)
register_exception_mapping(
    ExternalServiceError, status.HTTP_502_BAD_GATEWAY
)  # parent — register last
register_exception_mapping(DecryptionError, status.HTTP_500_INTERNAL_SERVER_ERROR)
register_exception_mapping(RateLimitError, status.HTTP_429_TOO_MANY_REQUESTS)
# Auth — register the revoked subclass before its parent so a revoked
# key returns ``API_KEY_REVOKED`` (not the parent's generic code) even
# though both resolve to 401.
register_exception_mapping(APIKeyRevokedError, status.HTTP_401_UNAUTHORIZED)
register_exception_mapping(
    AuthenticationFailedError, status.HTTP_401_UNAUTHORIZED
)
register_exception_mapping(PermissionDeniedError, status.HTTP_403_FORBIDDEN)
# Defensive parent fallbacks — registered last so they only catch
# subclasses added later without their own explicit mapping. Both
# resolve to 500 (matching the class attr); the value of registering
# them is making the fallback explicit instead of relying on the
# class-attr branch in custom_error_handler.
register_exception_mapping(RepositoryError, status.HTTP_500_INTERNAL_SERVER_ERROR)
register_exception_mapping(InfrastructureError, status.HTTP_500_INTERNAL_SERVER_ERROR)
