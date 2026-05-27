"""``RateLimitHeadersMiddleware`` — copies ``request.state.throttle_meta`` to headers.

The ``rate_limit`` FastAPI dependency stores a ``ThrottleResult`` on
``request.state.throttle_meta`` after each check; this middleware reads
that value (if any) and emits the standard ``X-RateLimit-*`` triple on
the outgoing response. Disabled via ``CoreSettings.rate_limit_headers_enabled=False``.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.core.resilience.throttle.base import ThrottleResult
from src.core.runtime import get_settings


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    """Copy ``request.state.throttle_meta`` to ``X-RateLimit-*`` response headers."""

    async def dispatch(self, request: Request, call_next):
        """Run the next handler and stamp rate-limit headers on the response.

        Args:
            request: Incoming Starlette request.
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The response with rate-limit headers (if enabled).
        """
        response = await call_next(request)
        if not get_settings().rate_limit_headers_enabled:
            return response
        meta = getattr(request.state, "throttle_meta", None)
        if isinstance(meta, ThrottleResult):
            response.headers["X-RateLimit-Limit"] = str(meta.limit)
            response.headers["X-RateLimit-Remaining"] = str(meta.remaining)
            response.headers["X-RateLimit-Reset"] = str(meta.reset_at)
        return response
