"""Import every ORM model so ``BaseModel.metadata`` is fully populated.

Used by the initial-DDL script (``src/management/init_db.py``) to discover every
table before issuing ``metadata.create_all``. Add new models to
``src.model`` and they'll be picked up automatically via that package's
``__init__`` re-exports.
"""

from src.model import Item

__all__ = ["Item"]
