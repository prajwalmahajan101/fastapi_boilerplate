"""``Item`` — example ORM model.

A minimal entity that demonstrates how to extend :class:`NamedBaseModel`
(which already supplies ``id``, ``created_at``, ``updated_at``,
``is_active``, ``notes``, plus a human-readable ``name`` and a unique
business ``code``). Delete this and add your own models under
``src.model`` — they're auto-discovered by ``src.db.tables``.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.base.model import NamedBaseModel


class Item(NamedBaseModel):
    """A demo catalogue item keyed on the inherited unique ``code``."""

    __tablename__ = "items"

    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


__all__ = ["Item"]
