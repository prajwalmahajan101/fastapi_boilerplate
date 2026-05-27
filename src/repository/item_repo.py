"""``ItemRepository`` — async CRUD over the ``items`` table.

Inherits the full :class:`BaseRepository` surface (``get_by_id`` / ``list``
/ ``list_paginated`` / ``add`` / ``update`` / ``delete_hard`` …). Add only
the bespoke queries this model needs — here, a lookup by the unique
business ``code``. Repositories own SQL; the surrounding transaction
boundary and hooks live in the service layer.
"""

from __future__ import annotations

from sqlalchemy import select

from src.core.base.repository import BaseRepository
from src.model.item import Item


class ItemRepository(BaseRepository[Item]):
    """Async CRUD over :class:`Item`."""

    model = Item

    async def get_by_code(self, code: str) -> Item | None:
        """Return the item matching the unique business ``code``.

        Args:
            code: Unique business identifier.

        Returns:
            The matching item, or ``None`` if no match.
        """
        stmt = select(Item).where(Item.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = ["ItemRepository"]
