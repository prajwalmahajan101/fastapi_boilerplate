"""Bounded background dispatch for ``ApiLog`` persistence.

Holds the audit-log :class:`FireAndForgetQueue` and the persist sink
that drains into the configured repository. Pulled out of
``api_log.decorators`` so the queue can be reused (e.g. by middleware)
without dragging in the decorator code.
"""

from __future__ import annotations

from typing import Any

from src.core.api_log.models import ApiLog
from src.core.utils.fire_and_forget import FireAndForgetQueue, register
from src.core.utils.logging import get_logger

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


__all__ = ["fire_and_forget", "persist_log"]
