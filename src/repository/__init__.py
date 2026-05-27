"""Data-access layer — one repository per aggregate root.

Repositories wrap :class:`src.core.base.repository.BaseRepository` and own
the SQL. Services call them inside an ``atomic`` transaction block.
"""

from src.repository.item_repo import ItemRepository

__all__ = ["ItemRepository"]
