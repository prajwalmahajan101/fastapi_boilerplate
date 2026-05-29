"""FastAPI ``Depends`` factory for throttling.

``rate_limit(scope, rate)`` returns a dependency that:
    1. resolves the ``(identifier, limit, window_seconds)`` triple from
       the request via the scope object;
    2. calls the process-wide throttle backend;
    3. stores the ``ThrottleResult`` on ``request.state.throttle_meta`` so
       ``RateLimitHeadersMiddleware`` can emit ``X-RateLimit-*`` headers;
    4. raises :class:`RateLimitError` with the throttle decision when the
       bucket is full. The central exception handler renders the standard
       ``ErrorEnvelope`` and attaches ``Retry-After`` + ``X-RateLimit-*``
       headers from ``RateLimitError.response_headers()``.

Usage::

    @router.get("/items", dependencies=[Depends(rate_limit("user_tier", "100/min"))])
    async def list_items(...):
        ...
"""

from __future__ import annotations

from typing import Callable

from fastapi import Request

from src.core.exceptions.rate_limit import RateLimitError
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
            RateLimitError: When the throttle backend rejects the call.
                The central handler renders a 429 ``ErrorEnvelope`` and
                attaches ``Retry-After`` + ``X-RateLimit-*`` headers.
        """
        identifier, limit, window = scope_obj.identify(request, rate)
        throttle = await get_throttle()
        result = await throttle.check(identifier, limit=limit, window_seconds=window)

        request.state.throttle_meta = result

        if not result.allowed:
            raise RateLimitError(
                limit=result.limit,
                window_seconds=window,
                retry_after=int(result.retry_after),
                remaining=result.remaining,
                reset_at=result.reset_at,
            )

    return dependency
