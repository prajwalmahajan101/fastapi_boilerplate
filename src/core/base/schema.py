"""``BaseSchema`` — Pydantic v2 base for all request/response DTOs.

Defaults that match what nearly every project wants:
    * ``from_attributes=True`` — populate from SQLAlchemy / ORM instances;
    * ``populate_by_name=True`` — accept payload keys by the Python
      attribute name. Subclasses that need to accept aliases (e.g.
      camelCase input) add an ``alias_generator`` to their own
      ``model_config``;
    * ``str_strip_whitespace=True`` — trim leading/trailing whitespace
      on every string field at validation time.

Outbound JSON is snake_case at every call site — the response
factories call ``model_dump(mode="json")`` without ``by_alias=True``.
A schema that needs camelCase output should declare its own
``alias_generator`` AND set ``by_alias=True`` when serialising.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base for every Pydantic schema used in the project."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )
