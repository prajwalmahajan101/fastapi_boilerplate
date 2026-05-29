"""PostgreSQL API log backend, sharing the application's engine.

Accepts an externally-built ``AsyncEngine`` (the one the application
already uses for ``db_dsn``) and never disposes it — disposal is handled
by ``core.utils.db.dispose_all_engines`` at lifespan shutdown. Schema is
defined in ``core.api_log.table``; manage via Alembic.

``save`` does not write a row inline: it hands the :class:`ApiLog` to
an in-memory queue that a background drain task flushes as a single
multi-row ``INSERT ... ON CONFLICT DO NOTHING``. That keeps the audit
subsystem off the request-path pool — one transaction per ``batch_size``
rows (or ``flush_interval_s`` of idle), not one per row. Queue overflow
drops the newest row with a warning so a degraded Postgres can never
back-pressure the producer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.api_log.models import ApiLog
from src.core.api_log.repository import ApiLogRepository
from src.core.api_log.table import api_logs

logger = logging.getLogger(__name__)


def _row_values(log: ApiLog) -> dict[str, Any]:
    """Materialise an ``ApiLog`` into the column-keyed dict the INSERT uses.

    Args:
        log: A populated ``ApiLog`` record.

    Returns:
        Column-keyed values ready to feed into ``pg_insert(api_logs)``.
    """
    ts = log.timestamp.astimezone(UTC) if log.timestamp else None
    return {
        "log_id": log.log_id,
        "direction": log.direction.value,
        "service_name": log.service_name,
        "request_id": log.request_id,
        "environment": log.environment,
        "method": log.method,
        "url": log.url,
        "query_params": log.query_params,
        "request_headers": log.request_headers,
        "request_body": log.request_body,
        "response_status_code": log.response_status_code,
        "response_headers": log.response_headers,
        "response_body": log.response_body,
        "duration_ms": log.duration_ms,
        "error_type": log.error_type,
        "error_message": log.error_message,
        "timestamp": ts,
        "ttl_expires_at": log.ttl_expires_at,
        "extra": log.extra,
    }


class PostgresApiLogRepository(ApiLogRepository):
    """Persist audit logs to ``api_logs`` via a batched drain task.

    ``save`` is non-blocking — it enqueues the row into a bounded
    :class:`asyncio.Queue`. A background drain task started in
    :meth:`startup` accumulates up to ``batch_size`` rows (or waits at
    most ``flush_interval_s`` for the next row) and flushes them in a
    single transaction via ``pg_insert(...).on_conflict_do_nothing``.

    The drain task is the *only* place the engine pool is touched on
    the write path, so the audit subsystem no longer competes with
    request-path queries for connections under burst load.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        batch_size: int = 100,
        flush_interval_s: float = 1.0,
        queue_size: int = 5000,
    ) -> None:
        """Bind to a shared engine and configure the batching drain task.

        Args:
            engine: Shared application engine; this repository does
                not dispose it.
            batch_size: Max rows accumulated per flush.
            flush_interval_s: Max seconds the drain task will wait
                between flushes if the queue is producing slower than
                ``batch_size`` rows per interval.
            queue_size: Soft cap on buffered rows. Overflow drops the
                newest row with a warning.
        """
        self._engine = engine
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._queue: asyncio.Queue[ApiLog] = asyncio.Queue(maxsize=queue_size)
        self._drain_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def startup(self) -> None:
        """Start the background drain task that flushes batches."""
        if self._drain_task is not None:
            return
        self._stopping.clear()
        self._drain_task = asyncio.create_task(
            self._drain_loop(), name="api_log_postgres_drain"
        )
        logger.info(
            "PostgresApiLogRepository ready (batch_size=%d, interval=%.2fs).",
            self._batch_size,
            self._flush_interval_s,
        )

    async def shutdown(self) -> None:
        """Signal the drain task to flush remaining rows, then await it.

        Engine ownership belongs to the application lifespan, which
        calls ``core.utils.db.dispose_all_engines`` during shutdown —
        repositories that share the engine must not dispose it.
        """
        self._stopping.set()
        task = self._drain_task
        self._drain_task = None
        if task is not None:
            try:
                await task
            except Exception:  # noqa: BLE001 — drain task failures are already logged inline.
                logger.exception("PostgresApiLogRepository drain task crashed.")
        logger.info("PostgresApiLogRepository shutdown (engine left intact).")

    async def save(self, log: ApiLog) -> None:
        """Enqueue ``log`` for batched persistence — never raises.

        On queue overflow the row is dropped with a single warning line,
        matching the fire-and-forget contract: a degraded backend must
        never back-pressure the producer.

        Args:
            log: A populated ``ApiLog`` record.
        """
        try:
            self._queue.put_nowait(log)
        except asyncio.QueueFull:
            logger.warning(
                "PostgresApiLogRepository queue full (size=%d); dropping log.",
                self._queue.maxsize,
                extra={
                    "log_id": log.log_id,
                    "service_name": log.service_name,
                    "direction": log.direction.value,
                    "request_id": log.request_id,
                },
            )

    async def _drain_loop(self) -> None:
        """Pull up to ``batch_size`` rows per cycle and flush them in one INSERT.

        The loop blocks on ``asyncio.wait_for(queue.get, timeout=interval)``
        for the *first* row of each batch, then drains opportunistically
        without waiting so back-to-back enqueues collapse into a single
        statement. On shutdown the loop drains everything left in the
        queue (no fresh wait) and exits.
        """
        while not self._stopping.is_set():
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)
        # Final drain: flush whatever is buffered before exiting.
        remaining: list[ApiLog] = []
        while not self._queue.empty():
            remaining.append(self._queue.get_nowait())
            if len(remaining) >= self._batch_size:
                await self._flush(remaining)
                remaining = []
        if remaining:
            await self._flush(remaining)

    async def _collect_batch(self) -> list[ApiLog]:
        """Block for one row up to ``flush_interval_s``, then drain greedily.

        Returns:
            Up to ``batch_size`` :class:`ApiLog` instances; empty list
            when the interval expired with nothing in the queue.
        """
        try:
            first = await asyncio.wait_for(
                self._queue.get(), timeout=self._flush_interval_s
            )
        except asyncio.TimeoutError:
            return []
        batch = [first]
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush(self, batch: list[ApiLog]) -> None:
        """Issue one bulk ``INSERT ... ON CONFLICT DO NOTHING`` for ``batch``.

        Exceptions are caught and logged — never raised — so a transient
        Postgres outage stalls the drain loop for one batch only and
        the producer keeps enqueueing.

        Args:
            batch: Non-empty list of rows to persist.
        """
        try:
            values = [_row_values(log) for log in batch]
            stmt = (
                pg_insert(api_logs)
                .values(values)
                .on_conflict_do_nothing(index_elements=["log_id"])
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception:  # noqa: BLE001 — drain loop must not crash on backend failure.
            logger.exception(
                "Failed to flush %d API logs to Postgres",
                len(batch),
                extra={"batch_size": len(batch)},
            )
