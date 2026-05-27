"""``BaseCustomError`` — root of every project-defined exception.

The FastAPI exception handler (``core.exceptions.handlers``) recognises any
subclass of this class and rewraps it in the standard response envelope.
Subclasses contribute:
    * ``default_message`` — fallback when no message is passed;
    * ``error_code`` (optional) — UPPER_SNAKE_CASE; derived from class name
      otherwise (``EntityNotFoundError`` → ``ENTITY_NOT_FOUND``);
    * ``get_details()`` — structured context inserted into ``errors[].details``.
HTTP status comes from either a per-instance ``status_code=`` kwarg or a
class-level registration in the status-map (see ``handlers.register_exception_mapping``).
The ``request_id`` is captured from the async-local ContextVar at
construction time so the envelope can echo it back.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.context import get_request_id


class BaseCustomError(Exception):
    """Root of all project-defined exceptions."""

    default_message: str = "An unexpected error occurred."
    error_code: str | None = None
    status_code: int = 500

    def __init__(
        self, message: str | None = None, *, status_code: int | None = None
    ) -> None:
        """Capture message, status, and request_id at construction time.

        Args:
            message: Human-readable message; falls back to ``default_message``.
            status_code: Optional HTTP status override.
        """
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        self.request_id: str | None = get_request_id()
        super().__init__(self.message)

    def _derive_error_code(self) -> str:
        name = type(self).__name__
        name = re.sub(r"(Error|Exception)$", "", name)
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()

    def get_error_code(self) -> str:
        """Return the explicit error code or derive one from the class name.

        Returns:
            UPPER_SNAKE_CASE error code identifying the exception type.
        """
        return self.error_code or self._derive_error_code()

    def get_details(self) -> dict[str, Any] | None:
        """Return extra structured context for the error envelope.

        Returns:
            A details dict, or ``None`` if the exception carries no extras.
        """
        return None

    def to_error_dict(self) -> dict[str, Any]:
        """Render the exception as the envelope's ``errors[]`` entry.

        Returns:
            A dict with ``code``, ``message``, ``field``, and ``details``.
        """
        return {
            "code": self.get_error_code(),
            "message": self.message,
            "field": None,
            "details": self.get_details(),
        }
