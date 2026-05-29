"""Tiny task-tracking queue for background work that must not block callers.

Used by ``api_log.dispatch`` (and any background writer you add) so
audit writes and similar side effects don't slow down the request hot
path. Each queue owns its task set, registers a cleanup
callback per task, supports a bounded drain at shutdown, and caps the
in-flight set so a stalled downstream (e.g. Postgres degraded) cannot
leak unbounded memory — overflow drops the *newest* submission with a
single warning line per overflow event, matching the "audit must
never block the caller" contract.

The module also exposes a process-wide registry — every queue that
calls :func:`register` is reachable from :func:`drain_all`, so the
FastAPI lifespan has a single source of truth for "every
fire-and-forget queue this process owns" instead of importing three
separate drain helpers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PENDING = 1000
_REGISTERED: list[FireAndForgetQueue] = []


class FireAndForgetQueue:
    """Bounded queue of background ``asyncio.Task`` references with overflow drop."""

    def __init__(
        self, *, max_pending: int = _DEFAULT_MAX_PENDING, name: str = "anonymous"
    ) -> None:
        """Initialise an empty pending-task set with the given soft cap.

        Args:
            max_pending: Maximum number of concurrently in-flight tasks
                before new submissions are dropped with a warning. Set
                higher for hot paths (e.g. inbound + outbound audit log)
                and lower for steady-state writers (partner log).
            name: Short label used in log lines (overflow warning,
                drain debug). Defaults to ``"anonymous"`` — modules
                that want their queue to be drained by
                :func:`drain_all` should pass a meaningful name and
                wrap construction with :func:`register`.
        """
        self._pending: set[asyncio.Task[Any]] = set()
        self._max_pending = max_pending
        self._name = name

    def submit(self, coro: Any) -> None:
        """Schedule *coro* in the background, or drop it when at capacity.

        On overflow the coroutine is closed (so its frame is released)
        and a single ``logger.warning`` is emitted; the caller's hot
        path is never blocked. Operators should treat the warning as a
        signal that the downstream writer is slower than the producer.

        Args:
            coro: The coroutine to run in the background.
        """
        if len(self._pending) >= self._max_pending:
            logger.warning(
                "FireAndForgetQueue[%s] at capacity (%d); dropping task.",
                self._name,
                self._max_pending,
            )
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait up to ``timeout`` seconds for in-flight tasks to finish.

        Called from the FastAPI lifespan shutdown so the process does
        not exit while audit writes are still mid-flush. Stragglers are
        logged but not awaited beyond the timeout — shutdown is the
        priority.

        Args:
            timeout: Maximum seconds to wait for outstanding tasks.
        """
        if not self._pending:
            return
        logger.debug(
            "Draining %d pending background tasks (queue=%s)",
            len(self._pending),
            self._name,
        )
        _, pending = await asyncio.wait(self._pending, timeout=timeout)
        if pending:
            logger.warning(
                "Timed out draining %d background tasks (queue=%s)",
                len(pending),
                self._name,
            )


def register(queue: FireAndForgetQueue) -> FireAndForgetQueue:
    """Register *queue* so :func:`drain_all` will drain it at shutdown.

    Typical use: wrap a module-level ``FireAndForgetQueue`` construction
    so the FastAPI lifespan doesn't need a separate ``drain_pending_*``
    import per producer.

    ::

        _queue = register(FireAndForgetQueue(max_pending=500, name="auth"))

    Args:
        queue: The queue instance to register.

    Returns:
        ``queue`` unchanged, so the call is transparent at the
        assignment site.
    """
    _REGISTERED.append(queue)
    return queue


async def drain_all(timeout: float = 5.0) -> None:
    """Drain every registered queue concurrently.

    Wait up to ``timeout`` seconds per queue (the queues drain in
    parallel via :func:`asyncio.gather` so the total wall-time is
    bounded by ``timeout`` rather than ``len(_REGISTERED) * timeout``).

    Args:
        timeout: Maximum seconds to wait per queue.
    """
    if not _REGISTERED:
        return
    await asyncio.gather(*(q.drain(timeout) for q in _REGISTERED))


def _reset_registry() -> None:
    """Drop every registered queue — intended for test teardown only."""
    _REGISTERED.clear()


__all__ = ["FireAndForgetQueue", "drain_all", "register"]
