"""Async retry with exponential backoff + jitter.

Two decorators:
    * ``retry_with_exponential_backoff(...)`` — generic, takes explicit
      knobs. Mirrors the ignosis decorator API.
    * ``retry_on_failure(service_name)`` — resolves config from
      ``ResilienceRegistry``; pair with ``@circuit_breaker`` via the
      ``@resilient`` shorthand for retry+circuit composition.

Both detect coroutine functions and use ``asyncio.sleep`` / ``time.sleep``
appropriately. Jitter (0.5×–1.5×) prevents thundering-herd retries.
``ServiceUnavailableError`` is filtered out of ``retry_on`` so a retried
call cannot defeat an OPEN circuit breaker.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import random
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from src.core.exceptions.infrastructure import ServiceUnavailableError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def retry_with_exponential_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exponential_base: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    raise_on_failure: bool = True,
    on_error: Callable[[Exception, int], None] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorate a sync or async function to retry with exponential backoff.

    ``delay = min(base_delay * exponential_base**attempt, max_delay) * U(0.5, 1.5)``
    Total attempts = ``max_retries + 1``.

    Args:
        max_retries: Number of retry attempts after the initial call.
        base_delay: Initial backoff in seconds before the first retry.
        exponential_base: Multiplier per attempt
            (``delay = base_delay * exponential_base ** attempt``).
        max_delay: Cap on the un-jittered backoff in seconds.
        exceptions: Exception classes that trigger a retry; anything
            else propagates immediately.
        raise_on_failure: When ``True`` re-raise the last exception
            after retries are exhausted; when ``False`` return ``None``.
        on_error: Optional ``callable(exc, attempt_number)`` invoked
            after each failed attempt.

    Returns:
        The wrapping decorator.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        """Pick the sync or async retry wrapper based on ``func``'s shape.

        Args:
            func: The callable being wrapped.

        Returns:
            The wrapped callable, signature-compatible with ``func``.
        """
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                """Async wrapper that retries with jittered exponential backoff.

                Args:
                    *args: Positional arguments forwarded to ``func``.
                    **kwargs: Keyword arguments forwarded to ``func``.

                Returns:
                    Whatever the wrapped coroutine returned.

                Raises:
                    last_exc: The last caught exception, after retries
                        are exhausted and ``raise_on_failure`` is True.
                """
                last_exc: Exception | None = None
                for attempt in range(max_retries + 1):
                    try:
                        result = await func(*args, **kwargs)
                        if attempt > 0:
                            logger.info(
                                "Function '%s' succeeded on attempt %d",
                                func.__name__,
                                attempt + 1,
                            )
                        return result
                    except exceptions as exc:
                        last_exc = exc
                        if on_error:
                            try:
                                on_error(exc, attempt + 1)
                            except Exception as cb_exc:  # noqa: BLE001
                                logger.error("Error in on_error callback: %s", cb_exc)
                        if attempt < max_retries:
                            delay = min(
                                base_delay * (exponential_base**attempt), max_delay
                            ) * (0.5 + random.random())
                            logger.warning(
                                "'%s' failed (attempt %d/%d): %s — retrying in %.2fs",
                                func.__name__,
                                attempt + 1,
                                max_retries + 1,
                                exc,
                                delay,
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                "'%s' failed after %d attempts: %s",
                                func.__name__,
                                max_retries + 1,
                                exc,
                            )
                if raise_on_failure and last_exc is not None:
                    raise last_exc
                return None  # type: ignore[return-value]

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Sync wrapper that retries with jittered exponential backoff.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``.

            Returns:
                Whatever the wrapped function returned.

            Raises:
                last_exc: The last caught exception, after retries are
                    exhausted and ``raise_on_failure`` is True.
            """
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(
                            "Function '%s' succeeded on attempt %d",
                            func.__name__,
                            attempt + 1,
                        )
                    return result
                except exceptions as exc:
                    last_exc = exc
                    if on_error:
                        try:
                            on_error(exc, attempt + 1)
                        except Exception as cb_exc:  # noqa: BLE001
                            logger.error("Error in on_error callback: %s", cb_exc)
                    if attempt < max_retries:
                        delay = min(
                            base_delay * (exponential_base**attempt), max_delay
                        ) * (0.5 + random.random())
                        logger.warning(
                            "'%s' failed (attempt %d/%d): %s — retrying in %.2fs",
                            func.__name__,
                            attempt + 1,
                            max_retries + 1,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "'%s' failed after %d attempts: %s",
                            func.__name__,
                            max_retries + 1,
                            exc,
                        )
            if raise_on_failure and last_exc is not None:
                raise last_exc
            return None  # type: ignore[return-value]

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def retry_on_failure(service_name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Resolve retry config from ``ResilienceRegistry`` and apply it.

    Reads ``CoreSettings.resilience_defaults['retry']`` merged with any
    per-service overrides registered via
    ``resilience_registry.register_service(...)``. Filters
    ``ServiceUnavailableError`` from ``retry_on`` so a retried call
    cannot defeat an OPEN circuit breaker.

    The effective retry wrapper is built lazily on the first invocation
    and cached on the decoration's closure thereafter. This matches the
    rest of the resilience stack: ``ResilienceRegistry.register_service``
    raises once the breaker materialises, so post-startup config changes
    are not supported anywhere. Rebuilding the retry wrapper per call
    therefore bought nothing — the cache keeps every wrapped call out of
    ``copy.deepcopy(resilience_defaults)``.

    Args:
        service_name: Service tag registered with the resilience
            registry (e.g. ``"bhn_api"``, ``"s3"``).

    Returns:
        The wrapping decorator that resolves the config on first call.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        """Pick sync vs async wrapping for ``func``.

        Args:
            func: The callable being wrapped.

        Returns:
            The wrapped callable, signature-compatible with ``func``.
        """
        is_async = inspect.iscoroutinefunction(func)
        # Mutable closure cell — None until the first call resolves the
        # config from the registry and builds the retry wrapper.
        cached: list[Callable[..., Any] | None] = [None]

        def _get_decorated() -> Callable[..., Any]:
            """Return the cached retry wrapper, building it on first call.

            Returns:
                Either ``func`` unchanged (no retry classes after
                filtering) or ``func`` wrapped in
                :func:`retry_with_exponential_backoff`.
            """
            if cached[0] is not None:
                return cached[0]

            from src.core.resilience.registry import resilience_registry

            config = resilience_registry.get_config(service_name).get("retry", {})
            exception_classes: tuple[type[Exception], ...] = tuple(
                config.get("retry_on") or (Exception,)
            )
            exception_classes = tuple(
                cls
                for cls in exception_classes
                if not issubclass(cls, ServiceUnavailableError)
            )
            if not exception_classes:
                # Nothing to retry — cache func bare so future calls
                # still skip the registry lookup.
                cached[0] = func
                return func

            cached[0] = retry_with_exponential_backoff(
                max_retries=config.get("max_attempts", config.get("max_retries", 3)),
                base_delay=config.get("base_delay", config.get("wait_min", 1.0)),
                exponential_base=config.get("exponential_base", 2.0),
                max_delay=config.get("max_delay", config.get("wait_max", 10.0)),
                exceptions=exception_classes,
                raise_on_failure=True,
            )(func)
            return cached[0]

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                """Resolve the effective decorator and delegate (async).

                Args:
                    *args: Positional arguments forwarded to ``func``.
                    **kwargs: Keyword arguments forwarded to ``func``.

                Returns:
                    Whatever the wrapped coroutine returned.
                """
                return await _get_decorated()(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Resolve the effective decorator and delegate (sync).

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``.

            Returns:
                Whatever the wrapped function returned.
            """
            return _get_decorated()(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator
