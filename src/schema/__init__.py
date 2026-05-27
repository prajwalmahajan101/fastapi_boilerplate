"""Request/response schemas (Pydantic v2 DTOs).

One module per resource. Schemas extend
:class:`src.core.base.schema.BaseSchema`. Keep ORM models out of this
layer — schemas are the wire contract, models are persistence.
"""

from src.schema.item import ItemCreate, ItemRead, ItemUpdate

__all__ = ["ItemCreate", "ItemRead", "ItemUpdate"]
