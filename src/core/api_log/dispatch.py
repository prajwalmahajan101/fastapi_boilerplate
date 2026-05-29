"""Bounded background dispatch for ``ApiLog`` persistence.

Holds the audit-log :class:`FireAndForgetQueue` and the persist sink
that drains into the configured repository. Pulled out of
``api_log.decorators`` so the queue can be reused (e.g. by middleware)
without dragging in the decorator code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.api_log.models import ApiLog
from src.core.api_log.sanitizers import _UNSET
from src.core.utils.fire_and_forget import FireAndForgetQueue, register
from src.core.utils.logging import get_logger
from src.core.utils.timing import perf_timer

logger = get_logger(__name__)

# Generous cap because this path receives every
# inbound *and* every outbound HTTP call. The queue drops new
# submissions with a warning once it hits this many in-flight tasks.
# Registered so the lifespan's ``drain_all`` reaches it without an
# extra import.
_queue = register(FireAndForgetQueue(max_pending=2000, name="api_log"))


def fire_and_forget(coro: Any) -> None:
    """Submit ``coro`` to the bounded background queue.

    Submissions are dropped with a warning when the queue is at
    capacity — see :class:`FireAndForgetQueue`. The audit log must
    never block the inbound/outbound hot path.

    Args:
        coro: The persistence coroutine to schedule.
    """
    _queue.submit(coro)


async def persist_log(log: ApiLog) -> None:
    """Save ``log`` — never raises (fire-and-forget contract).

    The repository's ``save`` may raise on DB outages; logging the
    failure keeps the producer running and lets operators see the
    backend health without affecting the request path.

    Args:
        log: Populated ``ApiLog`` record to persist.
    """
    try:
        from src.core.api_log.factory import get_repository

        await get_repository().save(log)
    except Exception:  # noqa: BLE001 — fire-and-forget audit sink: a DB / backend outage must never propagate to the producer that has already returned.
        logger.exception("API log save failed", extra={"log_id": log.log_id})


@dataclass
class CaptureState:
    """Per-call state passed to a per-direction ``build_log`` closure.

    Both inbound and outbound decorators run the same skeleton — start
    a :func:`perf_timer`, ``await func``, catch any exception, and at
    the end submit ``persist_log(build_log(state))`` to the bounded
    background queue. The state object lets the shared skeleton return
    timing + result + exception to the per-direction builder without
    leaking the wrapper internals.

    Attributes:
        result: Whatever the wrapped function returned, or ``_UNSET``
            when the call raised.
        exc: The exception raised by the wrapped function, or ``None``
            on the success path.
        elapsed_ms: Wall time the wrapped call took, in milliseconds.
        extras: Optional per-direction context (e.g. a captured
            request body, a published meta dict) the outer wrapper
            stashes for the builder to read.
    """

    result: Any = _UNSET
    exc: Exception | None = None
    elapsed_ms: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


async def capture_and_dispatch(
    func: Callable[..., Awaitable[Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    build_log: Callable[[CaptureState], ApiLog],
) -> Any:
    """Run ``func`` under a timer and schedule an audit row for the call.

    Owns the shared wrapper shape used by both
    :func:`log_inbound_request` and :func:`log_outbound_request`:
    start a :func:`perf_timer`, await ``func``, capture any exception,
    and in ``finally`` submit ``persist_log(build_log(state))`` to the
    bounded background queue. ``build_log`` is the per-direction
    closure that materialises the :class:`ApiLog`; it receives the
    populated :class:`CaptureState` and returns a synchronous result.

    Args:
        func: The wrapped async callable to invoke.
        args: Positional arguments forwarded to ``func``.
        kwargs: Keyword arguments forwarded to ``func``.
        build_log: Per-direction closure that builds the ``ApiLog`` to
            persist from the populated :class:`CaptureState`.

    Returns:
        Whatever ``func`` returned.

    Raises:
        Exception: Re-raises whatever ``func`` raised, after the audit
            row is queued for persistence.
    """
    state = CaptureState()
    with perf_timer() as t:
        try:
            state.result = await func(*args, **kwargs)
            return state.result
        except Exception as exc:
            state.exc = exc
            raise
        finally:
            state.elapsed_ms = float(t.elapsed_ms)
            fire_and_forget(persist_log(build_log(state)))


__all__ = [
    "CaptureState",
    "capture_and_dispatch",
    "fire_and_forget",
    "persist_log",
]
