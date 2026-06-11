"""``@log_function`` decorator — async+sync entry/exit/error tracing.

Off by default; flipped on via ``CoreSettings.log_function_calls=True``
(typically ``LOG_FUNCTION_CALLS=true`` in dev). ERROR-level failure logs
fire unconditionally; entry/exit are DEBUG-only and zero-cost when the
flag is off.

Dormant: not currently applied to any request-path function. Uncovered
until a feature decorates a real call site; do not import from a
request-path file without adding a matching test. Tracked by
``tests/unit/scripts/test_no_dormant_imports.py``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, cast

from src.core.utils.logging import is_function_logging_enabled

P = ParamSpec("P")
R = TypeVar("R")


def _summarize(obj: Any, max_length: int = 200) -> str:
    """Compact string view of *obj* for inclusion in a log record.

    Avoids ``repr``ing entire collections (a 10 000-element list would
    blow the log line) by reporting just shape + length for
    dict/list/tuple. Scalars are stringified and truncated.

    Args:
        obj: Any value pulled from a function's args or result.
        max_length: Truncation length for scalar string forms.

    Returns:
        Single-line summary safe to embed in a log record.
    """
    try:
        if isinstance(obj, dict):
            return f"<dict with {len(obj)} keys>"
        if isinstance(obj, list):
            return f"<list with {len(obj)} items>"
        if isinstance(obj, tuple):
            return f"<tuple with {len(obj)} items>" if obj else "<tuple>"
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            text = str(obj)
            return text[:max_length] + "..." if len(text) > max_length else text
        return f"<{type(obj).__name__}>"
    except Exception:
        return "<unserializable>"


def _log_enter(
    log: logging.Logger,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Emit a DEBUG ``function_enter`` record (no-op when feature is off).

    Args:
        log: Logger to emit on (usually ``func``'s own module logger).
        func: The wrapped callable.
        args: Positional arguments captured by the decorator.
        kwargs: Keyword arguments captured by the decorator.
    """
    if not is_function_logging_enabled():
        return
    log.debug(
        "Executing %s",
        func.__name__,
        extra={
            "func_args": _summarize(args),
            "func_kwargs": _summarize(kwargs),
            "args_count": len(args),
            "kwargs_count": len(kwargs),
            "event": "function_enter",
        },
    )


def _log_exit(
    log: logging.Logger, func: Callable[..., Any], result: Any, start: float, end: float
) -> None:
    """Emit a DEBUG ``function_exit`` record with duration (no-op when off).

    Args:
        log: Logger to emit on.
        func: The wrapped callable.
        result: Value the callable returned.
        start: ``time.perf_counter()`` reading at entry.
        end: ``time.perf_counter()`` reading at exit.
    """
    if not is_function_logging_enabled():
        return
    duration = end - start
    log.debug(
        "Completed %s",
        func.__name__,
        extra={
            "duration_seconds": round(duration, 3),
            "duration_ms": round(duration * 1000, 2),
            "result_summary": _summarize(result),
            "result_type": type(result).__name__,
            "event": "function_exit",
        },
    )


def _log_error(
    log: logging.Logger,
    func: Callable[..., Any],
    start: float,
    end: float,
    exc: BaseException,
) -> None:
    """Emit ERROR + DEBUG records for a raised exception.

    The ERROR record is always emitted (so prod sees the failure even
    when function tracing is off); the DEBUG record adds the full stack
    trace via ``exc_info=True``.

    Args:
        log: Logger to emit on.
        func: The wrapped callable that raised.
        start: ``time.perf_counter()`` reading at entry.
        end: ``time.perf_counter()`` reading when ``exc`` propagated.
        exc: The exception that escaped the wrapped callable.
    """
    duration = end - start
    log.error(
        "Function %s failed: %s",
        func.__name__,
        type(exc).__name__,
        extra={
            "error_type": type(exc).__name__,
            "error_message": _summarize(str(exc), max_length=200),
            "duration_seconds": round(duration, 3),
            "event": "function_error",
        },
    )
    log.debug(
        "Function %s raised exception - full stack trace",
        func.__name__,
        extra={
            "duration_seconds": duration,
            "duration_ms": round(duration * 1000, 2),
            "error_type": type(exc).__name__,
            "error_message_full": str(exc),
            "error_class": exc.__class__.__name__,
            "error_module": type(exc).__module__,
            "event": "function_error_detailed",
        },
        exc_info=True,
    )


def log_function(
    logger: logging.Logger | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a callable to log entry, exit, and errors (sync or async).

    Args:
        logger: Logger to emit on. Defaults to the wrapped function's
            module logger, so each module's logs land under its own name.

    Returns:
        The wrapping decorator.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        """Pick the sync or async wrapper based on ``func``'s shape.

        Args:
            func: The callable being wrapped.

        Returns:
            The wrapped callable, preserving ``func``'s signature.
        """
        log = logger or logging.getLogger(func.__module__)

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                """Async wrapper: time the call, log around it, re-raise on error.

                Args:
                    *args: Positional arguments forwarded to ``func``.
                    **kwargs: Keyword arguments forwarded to ``func``.

                Returns:
                    Whatever the wrapped coroutine returned.

                Raises:
                    Exception: Re-raised after the ERROR/DEBUG log records.
                """
                start = time.perf_counter()
                _log_enter(log, func, args, kwargs)
                try:
                    result = await func(*args, **kwargs)
                    _log_exit(log, func, result, start, time.perf_counter())
                    return result
                except Exception as exc:
                    _log_error(log, func, start, time.perf_counter(), exc)
                    raise

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Sync wrapper: time the call, log around it, re-raise on error.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``.

            Returns:
                Whatever the wrapped function returned.

            Raises:
                Exception: Re-raised after the ERROR/DEBUG log records.
            """
            start = time.perf_counter()
            _log_enter(log, func, args, kwargs)
            try:
                result = func(*args, **kwargs)
                _log_exit(log, func, result, start, time.perf_counter())
                return result
            except Exception as exc:
                _log_error(log, func, start, time.perf_counter(), exc)
                raise

        return cast(Callable[P, R], sync_wrapper)

    return decorator
