"""Pydantic DTOs for the ``Item`` example.

Inbound schemas validate request bodies; the outbound schema shapes what
the route serialises into the response envelope's ``data``. All extend
:class:`src.core.base.schema.BaseSchema`, which enables ``from_attributes``
(populate straight from an ORM row) and strips whitespace on string fields.
"""

from __future__ import annotations

from pydantic import Field

from src.core.base.schema import BaseSchema


class ItemCreate(BaseSchema):
    """Request body for creating an item."""

    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1024)
    quantity: int = Field(default=0, ge=0)


class ItemUpdate(BaseSchema):
    """Request body for a partial update — every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    quantity: int | None = Field(default=None, ge=0)


class ItemRead(BaseSchema):
    """Response shape for an item (populated from the ORM row)."""

    id: int
    name: str
    code: str
    description: str | None
    quantity: int
    is_active: bool


__all__ = ["ItemCreate", "ItemRead", "ItemUpdate"]
