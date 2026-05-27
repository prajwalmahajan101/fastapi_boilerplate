"""Response envelope — single source of truth for every HTTP response body.

This module defines:

* :class:`ResponseEnvelope` — the generic Pydantic shape every endpoint
  body conforms to. Previously lived in ``src.core.base.response``.
* :class:`SuccessEnvelope` / :class:`ErrorEnvelope` — typed subclasses
  with ``success`` pinned via ``Literal`` and the unused fields locked
  off (``errors: None`` on success envelopes, ``data: None`` on error
  envelopes).
* Factory helpers (``SuccessResponse`` / ``ErrorResponse``) that
  **instantiate** the typed subclass and return a ready-to-use
  :class:`fastapi.responses.JSONResponse`. The Pydantic instance is the
  single source of truth for the wire shape — there is no hand-built
  dict.

Usage::

    return SuccessResponse(data=user.model_dump(), message="User created.", status_code=201)
    return ErrorResponse(message="Quota exceeded", errors=[...], status_code=429)
"""

from __future__ import annotations

import math
from typing import Any, Generic, Literal, Sequence, TypeVar

from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from src.core.context import get_request_id
from src.core.responses.schemas import ErrorDetail, PaginatedData

T = TypeVar("T")


# ── Base envelope ─────────────────────────────────────────────────────────


class ResponseEnvelope(BaseModel, Generic[T]):
    """Canonical envelope every HTTP response body conforms to.

    Concrete responses extend this class — :class:`SuccessEnvelope`,
    :class:`ErrorEnvelope`. The factory helpers below instantiate one of
    those subclasses so the schema declared here is *the* schema served
    on the wire.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    message: str
    data: T | None = None
    errors: list[ErrorDetail] | None = None
    request_id: str | None = None


# ── Typed subclasses ──────────────────────────────────────────────────────


class SuccessEnvelope(ResponseEnvelope[T], Generic[T]):
    """``success=True`` envelope. Carries optional ``data``, never ``errors``."""

    success: Literal[True] = True
    errors: None = None


class ErrorEnvelope(ResponseEnvelope[None]):
    """``success=False`` envelope. Carries ``errors``, never ``data``."""

    success: Literal[False] = False
    data: None = None
    errors: list[ErrorDetail]


# ── Internal helpers ──────────────────────────────────────────────────────


def _to_json(
    envelope: ResponseEnvelope[Any],
    *,
    status_code: int,
    headers: dict[str, str] | None,
) -> JSONResponse:
    """Serialise *envelope* to a :class:`JSONResponse`.

    Args:
        envelope: A populated :class:`ResponseEnvelope` (or subclass).
        status_code: HTTP status to return.
        headers: Optional response headers.

    Returns:
        Ready-to-return ``JSONResponse`` whose body is the envelope's
        ``model_dump(mode="json")`` output.
    """
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


def _coerce_error_list(
    errors: Sequence[ErrorDetail | dict[str, Any]],
) -> list[ErrorDetail]:
    """Normalise *errors* into a list of :class:`ErrorDetail` instances.

    Callers may pass either ``ErrorDetail`` instances or raw dicts shaped
    like ``{"code", "message", "field", "details"}`` — exception handlers
    historically build dicts. Both forms validate through this helper.

    Args:
        errors: Iterable of error entries (dicts or ``ErrorDetail``).

    Returns:
        List of validated ``ErrorDetail`` instances.
    """
    out: list[ErrorDetail] = []
    for entry in errors:
        if isinstance(entry, ErrorDetail):
            out.append(entry)
        else:
            out.append(ErrorDetail.model_validate(entry))
    return out


def _resolve_request_id(explicit: str | None) -> str | None:
    """Return *explicit* if set, else the current request-id context var.

    Args:
        explicit: Caller-supplied request id (may be ``None``).

    Returns:
        ``explicit`` when not ``None``; otherwise the value from the
        current request-id context (``None`` outside a request scope).
    """
    return explicit if explicit is not None else get_request_id()


# ── Public factories ──────────────────────────────────────────────────────


def SuccessResponse(  # noqa: N802 — factory function, capitalized for symmetry with helpers
    data: Any = None,
    *,
    message: str = "Success",
    status_code: int = status.HTTP_200_OK,
    headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Return a :class:`SuccessEnvelope` rendered as ``JSONResponse``.

    Args:
        data: Payload to surface as ``data`` in the envelope.
        message: Human-readable summary.
        status_code: HTTP status (defaults to 200).
        headers: Optional response headers.
        request_id: Override the request id (defaults to ``request_id_ctx``).

    Returns:
        JSON envelope with ``success=True``.
    """
    envelope: SuccessEnvelope[Any] = SuccessEnvelope[Any](
        message=message,
        data=data,
        request_id=_resolve_request_id(request_id),
    )
    return _to_json(envelope, status_code=status_code, headers=headers)


def PaginatedResponse(  # noqa: N802 — factory function, capitalized for symmetry
    items: Sequence[Any],
    *,
    page: int,
    size: int,
    total_count: int,
    message: str = "Success",
    status_code: int = status.HTTP_200_OK,
    headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Return a paginated :class:`SuccessEnvelope` rendered as ``JSONResponse``.

    Computes ``total_pages`` / ``has_prev`` / ``has_next`` from the
    inputs so route handlers only need to know the slice + the total
    count. Items must already be serialisable — the caller typically
    runs ``Model.model_dump(mode="json")`` for each ORM row before
    passing the list in.

    Args:
        items: The page slice as already-serialisable values.
        page: 1-indexed page number the caller requested.
        size: Page size the caller requested.
        total_count: Total rows across the full dataset (not just this slice).
        message: Human-readable summary.
        status_code: HTTP status (defaults to 200).
        headers: Optional response headers.
        request_id: Override the request id (defaults to ``request_id_ctx``).

    Returns:
        JSON envelope with ``success=True`` and ``data`` shaped as
        :class:`PaginatedData`.
    """
    total_pages = math.ceil(total_count / size) if size > 0 else 0
    payload = PaginatedData[Any](
        list=list(items),
        current_page=page,
        size=size,
        total_count=total_count,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
    )
    envelope: SuccessEnvelope[PaginatedData[Any]] = SuccessEnvelope[PaginatedData[Any]](
        message=message,
        data=payload,
        request_id=_resolve_request_id(request_id),
    )
    return _to_json(envelope, status_code=status_code, headers=headers)


def ErrorResponse(  # noqa: N802
    message: str,
    *,
    errors: Sequence[ErrorDetail | dict[str, Any]] | None = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Return an :class:`ErrorEnvelope` rendered as ``JSONResponse``.

    Args:
        message: Human-readable summary of the failure.
        errors: List of ``ErrorDetail`` instances or dicts shaped like
            ``{"code", "message", "field", "details"}``. Dicts are
            validated through :class:`ErrorDetail`.
        status_code: HTTP status (defaults to 400).
        headers: Optional response headers.
        request_id: Override the request id (defaults to ``request_id_ctx``).
            Exception handlers pass the snapshot captured on the raised
            ``BaseCustomError`` so the envelope echoes the request id from
            the moment the error was constructed.

    Returns:
        JSON envelope with ``success=False``.
    """
    envelope = ErrorEnvelope(
        message=message,
        errors=_coerce_error_list(errors or []),
        request_id=_resolve_request_id(request_id),
    )
    return _to_json(envelope, status_code=status_code, headers=headers)


__all__ = [
    "ErrorEnvelope",
    "ErrorResponse",
    "PaginatedResponse",
    "ResponseEnvelope",
    "SuccessEnvelope",
    "SuccessResponse",
]
