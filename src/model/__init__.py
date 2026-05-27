"""ORM models — re-exported so ``src.db.tables`` can register them all.

Add each new model here; ``src.db.tables`` imports from this package so
``BaseModel.metadata`` is fully populated for Alembic autogenerate and the
emergency ``init_db`` DDL bootstrap.
"""

from src.model.item import Item

__all__ = ["Item"]
