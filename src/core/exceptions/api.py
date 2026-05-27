"""``APIError`` — raised by ``AsyncAPIClient`` on outbound HTTP failures.

Kept separate from ``ExternalServiceError`` (the resilience-layer abstract
parent) so consumers that only care about HTTP-level details (status, body)
can catch it directly without pulling in the broader hierarchy.
"""

from __future__ import annotations

from typing import Any

from src.core.base.exception import BaseCustomError


class APIError(BaseCustomError):
    """Outbound HTTP call failed with a non-2xx status or transport error."""

    default_message = "HTTP request failed."
    error_code = "API_ERROR"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Capture HTTP status, response body, and structured details.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status to surface (defaults to 502).
            response_body: Raw response body, if available.
            details: Extra structured context.
        """
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.response_body = response_body
        self.details = details or {}

    def get_details(self) -> dict[str, Any]:
        """Return ``details`` merged with ``response_body`` and ``status_code``.

        Returns:
            A dict suitable for the envelope's ``errors[].details``.
        """
        out: dict[str, Any] = {**(self.details or {})}
        if self.response_body is not None:
            out["response_body"] = self.response_body
        if self.status_code is not None:
            out["status_code"] = self.status_code
        return out
