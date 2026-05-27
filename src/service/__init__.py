"""Business-logic layer — one service per aggregate root.

Services orchestrate repositories, enforce domain rules in pre/post hooks,
and are the only layer routes should call. They extend
:class:`src.core.base.service.BaseService` (or ``BaseNamedModelService``)
and never open their own transaction — the route boundary owns the
``async with atomic(session):`` block.
"""

from src.service.item_service import ItemService

__all__ = ["ItemService"]
