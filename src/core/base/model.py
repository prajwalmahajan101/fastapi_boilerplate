"""Async SQLAlchemy declarative bases — ``BaseModel`` and ``NamedBaseModel``.

Mirrors the Django ``BaseModel`` audit shape minus the user-FK columns
(auth is intentionally deferred — see the plan). Every concrete model
inherits ``id``, ``created_at``, ``updated_at``, ``is_active`` (indexed
for the soft-delete cascade), and a free-form ``notes`` JSONB slot.

When auth lands, add an ``AuditedMixin`` here with ``created_by_id`` and
``updated_by_id`` columns + relationships; existing models can opt in via
a migration without touching ``BaseModel``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BaseModel(AsyncAttrs, DeclarativeBase):
    """Abstract base — every model gets id + audit timestamps + soft-delete flag."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Compact debug representation including the model class and id.

        Returns:
            String of the form ``<ClassName id=N>``.
        """
        return f"<{self.__class__.__name__} id={self.id}>"


class NamedBaseModel(BaseModel):
    """Top-level entities that need a human-readable name + unique business code."""

    __abstract__ = True

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
