"""FastAPI ``Depends`` factory for throttling.

``rate_limit(scope, rate)`` returns a dependency that:
    1. resolves the ``(identifier, limit, window_seconds)`` triple from
       the request via the scope object;
    2. calls the process-wide throttle backend;
    3. stores the ``ThrottleResult`` on ``request.state.throttle_meta`` so
       ``RateLimitHeadersMiddleware`` can emit ``X-RateLimit-*`` headers;
    4. raises ``HTTPException(429)`` with ``Retry-After`` if not allowed.

Usage::

    @router.get("/items", dependencies=[Depends(rate_limit("user_tier", "100/min"))])
    async def list_items(...):
        ...
"""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request, status

from src.core.resilience.throttle.provider import get_throttle
from src.core.resilience.throttle.scopes import _BaseScope, resolve_scope


def rate_limit(scope: str | _BaseScope, rate: str) -> Callable:
    """Return a FastAPI dependency that enforces the given scope+rate.

    Args:
        scope: Either a built-in scope key (``"burst"``, ``"endpoint"``,
            …) or a custom ``_BaseScope`` instance.
        rate: Rate string accepted by :func:`parse_rate`.

    Returns:
        A FastAPI dependency callable.
    """
    scope_obj = resolve_scope(scope)

    async def dependency(request: Request) -> None:
        """Run the throttle check and raise 429 when the bucket is full.

        Args:
            request: Incoming FastAPI request.

        Raises:
            HTTPException: 429 with ``Retry-After`` + ``X-RateLimit-*``
                headers when the throttle backend rejects.
        """
        identifier, limit, window = scope_obj.identify(request, rate)
        throttle = await get_throttle()
        result = await throttle.check(identifier, limit=limit, window_seconds=window)

        request.state.throttle_meta = result

        if not result.allowed:
            headers = {
                "Retry-After": str(max(1, int(result.retry_after))),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
                "X-RateLimit-Reset": str(result.reset_at),
            }
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit}/{window}s).",
                headers=headers,
            )

    return dependency
