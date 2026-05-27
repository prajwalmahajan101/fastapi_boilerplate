"""PostgreSQL API log backend, sharing the application's engine.

Accepts an externally-built ``AsyncEngine`` (the one the application
already uses for ``db_dsn``) and never disposes it — disposal is handled
by ``core.utils.db.dispose_all_engines`` at lifespan shutdown. Schema is
defined in ``core.api_log.table``; manage via Alembic.
"""

from __future__ import annotations

import logging
from datetime import UTC

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.api_log.models import ApiLog
from src.core.api_log.repository import ApiLogRepository
from src.core.api_log.table import api_logs

logger = logging.getLogger(__name__)


class PostgresApiLogRepository(ApiLogRepository):
    """Persist audit logs to ``api_logs`` via the application's shared engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the repository to an externally-owned ``AsyncEngine``.

        Args:
            engine: Shared application engine; this repository does
                not dispose it.
        """
        self._engine = engine

    async def startup(self) -> None:
        """Mark the repository ready — engine is shared so no init runs here."""
        logger.info("PostgresApiLogRepository ready (sharing application engine).")

    async def shutdown(self) -> None:
        # Engine ownership is shared with the application; disposal is the
        # caller's responsibility (via core.utils.db.dispose_all_engines).
        """Tear down without disposing the shared engine.

        Engine ownership belongs to the application lifespan, which
        calls ``core.utils.db.dispose_all_engines`` during shutdown —
        repositories that share the engine must not dispose it.
        """
        logger.info("PostgresApiLogRepository shutdown (engine left intact).")

    async def save(self, log: ApiLog) -> None:
        """Persist ``log`` via a single ``INSERT ... ON CONFLICT DO NOTHING``.

        ``log_id`` is the conflict target so retries do not duplicate
        rows. Exceptions are caught and logged — never raised — so the
        fire-and-forget contract holds.

        Args:
            log: A populated ``ApiLog`` record.
        """
        ts = log.timestamp.astimezone(UTC) if log.timestamp else None
        values = {
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
        try:
            stmt = (
                pg_insert(api_logs)
                .values(values)
                .on_conflict_do_nothing(index_elements=["log_id"])
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception:
            logger.exception(
                "Failed to write API log to Postgres",
                extra={"log_id": log.log_id},
            )
