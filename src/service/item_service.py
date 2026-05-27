"""``ItemService`` — business logic for the ``Item`` example.

Extends :class:`BaseNamedModelService` (the ``NamedBaseModel`` variant of
:class:`BaseService`), so it inherits ``create`` / ``update`` / ``delete``
with pre/post hooks, the soft-delete cascade, ``get_by_id_or_fail``, and
``get_by_code`` / ``get_by_code_or_fail`` for the unique business ``code``.

Write methods do not open their own transaction — the route wraps the unit
of work in ``async with atomic(session):``. This service overrides
``pre_create`` to demonstrate the hook surface (guarding against a
duplicate ``code`` with a domain :class:`ValidationError`).
"""

from __future__ import annotations

from typing import Any

from src.core.base.service import BaseNamedModelService
from src.core.exceptions.validation import ValidationError
from src.model.item import Item
from src.repository.item_repo import ItemRepository


class ItemService(BaseNamedModelService[Item]):
    """Async service over :class:`Item`."""

    model = Item
    repository_cls = ItemRepository
    #: Only these fields may be used as ``list(filters=...)`` keys.
    allowed_filter_fields = frozenset({"name", "code", "is_active"})

    async def pre_create(
        self, data: dict[str, Any], user: Any | None = None
    ) -> dict[str, Any]:
        """Reject a create that reuses an existing ``code``.

        The DB unique constraint is the real guard; this hook turns the
        race-free common case into a clean 400 instead of a 500 from the
        integrity error.

        Args:
            data: Field values for the new item.
            user: Acting user (optional; auth not wired in the boilerplate).

        Returns:
            The unmodified data dict.

        Raises:
            ValidationError: If an item with the same ``code`` already exists.
        """
        if await self.exists(code=data["code"]):
            raise ValidationError(
                f"An item with code '{data['code']}' already exists.",
                details={"field": "code"},
            )
        return data


__all__ = ["ItemService"]
