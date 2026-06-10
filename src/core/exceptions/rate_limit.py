"""Rate-limit exception — surfaces throttle rejections through the standard envelope.

Raised by the ``rate_limit`` FastAPI dependency from
``resilience_kit.adapters.fastapi`` instead of a raw
``fastapi.HTTPException``, so the 429 response shape matches the
``ErrorEnvelope`` contract and carries the same ``Retry-After`` /
``X-RateLimit-*`` headers.
"""

from __future__ import annotations

from typing import Any

from src.core.base.exception import BaseCustomError


class RateLimitError(BaseCustomError):
    """Caller exceeded the configured rate limit for the route / scope.

    Carries the throttle decision so the central handler can:
        * emit ``Retry-After`` and ``X-RateLimit-*`` headers, and
        * expose ``limit`` / ``window_seconds`` / ``retry_after`` /
          ``remaining`` / ``reset_at`` under ``errors[0].details``.
    """

    default_message = "Rate limit exceeded."
    error_code = "RATE_LIMITED"
    status_code = 429

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        retry_after: int,
        remaining: int = 0,
        reset_at: int = 0,
        message: str | None = None,
    ) -> None:
        """Capture the throttle decision for headers + details.

        Args:
            limit: Maximum requests allowed in the window.
            window_seconds: Window length in seconds.
            retry_after: Seconds the caller should wait before retrying.
            remaining: Requests remaining in the current window (0 on reject).
            reset_at: Unix timestamp at which the window resets.
            message: Optional override message.
        """
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = max(1, int(retry_after))
        self.remaining = remaining
        self.reset_at = reset_at
        super().__init__(message or f"Rate limit exceeded ({limit}/{window_seconds}s).")

    def get_details(self) -> dict[str, Any]:
        """Return the throttle decision for the envelope details.

        Returns:
            Dict with ``limit``, ``window_seconds``, ``retry_after``,
            ``remaining``, and ``reset_at``.
        """
        return {
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "retry_after": self.retry_after,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
        }

    def response_headers(self) -> dict[str, str]:
        """Return the rate-limit response headers for the central handler.

        Returns:
            ``Retry-After`` plus ``X-RateLimit-Limit/Remaining/Reset``.
        """
        return {
            "Retry-After": str(self.retry_after),
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
        }
