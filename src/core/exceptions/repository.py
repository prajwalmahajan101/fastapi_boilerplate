"""Data-access exceptions — missing rows."""

from __future__ import annotations

from typing import Any

from src.core.base.exception import BaseCustomError


class RepositoryError(BaseCustomError):
    """Base for any failure rooted in stored data or the ORM layer."""

    default_message = "A data access error occurred."
    error_code = "REPOSITORY_ERROR"
    status_code = 500


class EntityNotFoundError(RepositoryError):
    """A lookup by primary key returned nothing."""

    default_message = "Entity not found."
    error_code = "ENTITY_NOT_FOUND"
    status_code = 404

    def __init__(self, entity_name: str, entity_id: int | str) -> None:
        """Capture which entity type and id were missing.

        Args:
            entity_name: Human-readable model name (``"Product"``).
            entity_id: Primary key that was looked up.
        """
        self.entity_name = entity_name
        self.entity_id = entity_id
        super().__init__(f"{entity_name} with id={entity_id} not found.")

    def get_details(self) -> dict[str, Any]:
        """Return entity name and id for the envelope details.

        Returns:
            Dict with ``entity_name`` and ``entity_id``.
        """
        return {"entity_name": self.entity_name, "entity_id": self.entity_id}
