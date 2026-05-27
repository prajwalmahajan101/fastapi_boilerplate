"""Async factory for the configured API log backend.

Shares the application's database engine — for ``api_log_backend=postgres``
the repository wraps the engine returned by ``core.utils.db.get_app_engine``
(itself DSN-cached), so the audit log uses the same connection pool the
application already opened against ``CoreSettings.db_dsn``.
"""

from __future__ import annotations

import logging

from src.core.api_log.backends.noop import NoopApiLogRepository
from src.core.api_log.repository import ApiLogRepository
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)

_repository: ApiLogRepository | None = None


async def init_repository() -> None:
    """Create + start the audit-log backend configured on ``CoreSettings``."""
    global _repository
    settings = get_settings()
    backend = settings.api_log_backend

    if backend == "postgres":
        if not settings.db_dsn:
            logger.error(
                "api_log_backend=postgres but db_dsn is not set; falling back to noop."
            )
            _repository = NoopApiLogRepository()
        else:
            from src.core.api_log.backends.postgres import PostgresApiLogRepository
            from src.core.utils.db import get_app_engine

            engine = await get_app_engine(settings)
            _repository = PostgresApiLogRepository(engine)
    else:
        if backend != "noop":
            logger.warning("Unknown api_log_backend '%s'; using noop.", backend)
        _repository = NoopApiLogRepository()

    await _repository.startup()
    logger.info("API log repository ready (backend=%s).", backend)


def get_repository() -> ApiLogRepository:
    """Return the active repository (NoOp if ``init_repository`` not called).

    Returns:
        The configured backend instance, or a fresh ``NoopApiLogRepository``
        when ``init_repository`` hasn't run yet — keeps module-load-time
        decorator imports safe to invoke before lifespan startup.
    """
    if _repository is None:
        return NoopApiLogRepository()
    return _repository


async def close_repository() -> None:
    """Shutdown the active repository (engine disposal is centralised)."""
    global _repository
    if _repository is not None:
        await _repository.shutdown()
        _repository = None
        logger.info("API log repository closed.")


def _reset_for_tests() -> None:
    """Drop the active repository without calling shutdown — test teardown only."""
    global _repository
    _repository = None
