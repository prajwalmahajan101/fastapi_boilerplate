"""``@resilient`` and ``@circuit_breaker`` — async-aware composition.

``@circuit_breaker(name)`` wraps a function in the per-service breaker.
``@resilient(name)`` composes circuit breaker (outer) over retry (inner),
which is the typical "make this outbound call resilient" decorator.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from src.core.resilience.retry import retry_on_failure

P = ParamSpec("P")
T = TypeVar("T")


def circuit_breaker(service_name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Wrap a function in the per-service circuit breaker.

    Calls flow through ``ResilienceRegistry.get_breaker(service_name)``;
    one breaker per ``service_name`` is shared across decorated
    functions, so all calls to (say) ``"bhn_api"`` share state.

    Args:
        service_name: Service tag registered with the resilience registry.

    Returns:
        The wrapping decorator.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        """Pick the sync or async wrapper based on ``func``'s shape.

        Args:
            func: The callable being wrapped.

        Returns:
            The wrapped callable, signature-compatible with ``func``.
        """
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                """Resolve the breaker and dispatch the call (async path).

                Args:
                    *args: Positional arguments forwarded to ``func``.
                    **kwargs: Keyword arguments forwarded to ``func``.

                Returns:
                    Whatever the wrapped coroutine returned.
                """
                from src.core.resilience.registry import resilience_registry

                breaker = await resilience_registry.get_breaker(service_name)
                return await breaker.call(func, *args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            # Sync path delegates via a fresh event loop only if there is
            # no running loop. In an async app, prefer wrapping async functions.
            """Resolve the breaker and dispatch the call (sync path).

            Spawns a fresh event loop only when none is running so the
            decorator works in scripts as well as async services.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``.

            Returns:
                Whatever the wrapped function returned.
            """
            import asyncio

            from src.core.resilience.registry import resilience_registry

            async def _runner() -> Any:
                """Coroutine wrapper used by both sync entry paths.

                Returns:
                    Whatever the wrapped function returned.
                """
                breaker = await resilience_registry.get_breaker(service_name)
                return await breaker.call(func, *args, **kwargs)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_runner())
            return loop.run_until_complete(_runner())

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def resilient(service_name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Compose circuit breaker over retry — the standard outbound wrap.

    Outer breaker decides whether to attempt at all; the inner retry
    handles transient blips inside an attempt. Failures that exhaust
    retries propagate to the breaker which then counts them toward the
    OPEN threshold.

    Args:
        service_name: Service tag registered with the resilience registry.

    Returns:
        The wrapping decorator.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        """Apply retry-then-breaker around ``func`` (sync or async).

        Args:
            func: The callable being wrapped.

        Returns:
            The wrapped callable, signature-compatible with ``func``.
        """
        retried = retry_on_failure(service_name)(func)
        protected = circuit_breaker(service_name)(retried)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Forward to the composed sync wrapper.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``.

            Returns:
                Whatever the wrapped callable returned.
            """
            return protected(*args, **kwargs)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                """Await the composed coroutine wrapper.

                Args:
                    *args: Positional arguments forwarded to ``func``.
                    **kwargs: Keyword arguments forwarded to ``func``.

                Returns:
                    Whatever the wrapped coroutine returned.
                """
                return await protected(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        return wrapper  # type: ignore[return-value]

    return decorator
