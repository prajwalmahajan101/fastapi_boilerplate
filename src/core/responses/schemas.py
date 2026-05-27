"""Pydantic sub-shapes referenced by the response envelope.

Kept separate from ``envelope.py`` because the outer envelopes import
from here, not the other way round. Add new sub-shapes (e.g. a
pagination block) here when they need to appear inside an envelope's
``data`` slot without coupling ``envelope.py`` to every payload type.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """One entry in the ``errors`` array of an error envelope."""

    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


class PaginatedData(BaseModel, Generic[T]):
    """``data`` payload of a paginated success envelope.

    Carries the page slice plus everything the caller needs to navigate
    without re-querying the resource: ``has_prev`` / ``has_next`` flag
    boundaries, and ``total_count`` / ``total_pages`` describe the full
    dataset behind this slice. ``current_page`` is 1-indexed.

    The ``list`` field intentionally shadows the builtin name only
    inside this model body — every paginated route serialises the slice
    through :func:`src.core.responses.PaginatedResponse`, which builds
    the instance via keyword arguments.
    """

    list: list[T]
    current_page: int
    size: int
    total_count: int
    total_pages: int
    has_prev: bool
    has_next: bool


__all__ = ["ErrorDetail", "PaginatedData"]
