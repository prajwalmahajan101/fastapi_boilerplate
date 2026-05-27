"""``ValidationError`` — semantic validation failures from inside the service layer.

Distinct from FastAPI's ``RequestValidationError`` (request-payload schema
mismatch); use ``ValidationError`` for business-rule rejections that pass
the schema check but violate domain invariants.
"""

from __future__ import annotations

from typing import Any

from src.core.base.exception import BaseCustomError


class ValidationError(BaseCustomError):
    """Semantic / business-rule validation failed."""

    default_message = "Validation failed."
    error_code = "VALIDATION_ERROR"
    status_code = 400

    def __init__(
        self,
        message: str | None = None,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record the optional field name and structured details.

        Args:
            message: Human-readable description of the failure.
            field: Dotted-path identifier of the offending field (if any).
            details: Extra structured context.
        """
        super().__init__(message)
        self.field = field
        self.details = details or {}

    def get_details(self) -> dict[str, Any] | None:
        """Return the details dict, or ``None`` if empty.

        Returns:
            The details payload or ``None``.
        """
        return self.details or None

    def to_error_dict(self) -> dict[str, Any]:
        """Override to surface the offending ``field`` in the envelope.

        Returns:
            Envelope-shaped dict with ``field`` set.
        """
        return {
            "code": self.get_error_code(),
            "message": self.message,
            "field": self.field,
            "details": self.get_details(),
        }
