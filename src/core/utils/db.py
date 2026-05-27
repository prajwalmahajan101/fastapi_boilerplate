"""Async SQLAlchemy engine cache + session helpers.

Single source of truth for database engines in the application. Engines
are cached by DSN, so both the application's request-scoped sessions and
the API audit log Postgres backend share one connection pool when they
read ``CoreSettings.db_dsn``. ``dispose_all_engines`` is the single owner
of disposal at lifespan shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.runtime import get_settings
from src.core.settings import CoreSettings

logger = logging.getLogger(__name__)

_engines: dict[str, AsyncEngine] = {}
_engine_lock: asyncio.Lock = asyncio.Lock()


def _normalise_dsn(dsn: str) -> str:
    """Promote ``postgresql://`` to ``postgresql+asyncpg://`` (asyncpg driver).

    Args:
        dsn: Raw DSN as provided by config / env.

    Returns:
        The DSN forced onto the asyncpg driver if it had no explicit one.
    """
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


def _mask_dsn(dsn: str) -> str:
    """Render a DSN safely for logs (password redacted).

    Args:
        dsn: Raw DSN.

    Returns:
        The DSN with password masked; ``"<unrenderable dsn>"`` if the
        string couldn't be parsed.
    """
    try:
        return make_url(dsn).render_as_string(hide_password=True)
    except Exception:
        return "<unrenderable dsn>"


async def get_async_engine(
    dsn: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
    connect_timeout: int = 5,
    statement_timeout_ms: int = 30_000,
) -> AsyncEngine:
    """Return the cached engine for ``dsn``, creating it on first use.

    Subsequent calls with the same DSN ignore the ``pool_*`` / timeout
    kwargs and return the existing engine — first call wins. Call
    ``dispose_all_engines`` to rebuild with different parameters.

    Args:
        dsn: Connection string (driver scheme normalised internally).
        pool_size: Steady-state pool size.
        max_overflow: Additional connections beyond ``pool_size`` under
            load.
        pool_pre_ping: When ``True``, validate each checkout with a
            ``SELECT 1`` so stale connections are recycled.
        connect_timeout: Per-connection timeout (seconds).
        statement_timeout_ms: Per-statement timeout sent as
            ``server_settings.statement_timeout``.

    Returns:
        The shared ``AsyncEngine`` for this DSN.
    """
    normalised = _normalise_dsn(dsn)
    cached = _engines.get(normalised)
    if cached is not None:
        return cached

    async with _engine_lock:
        if normalised in _engines:
            return _engines[normalised]

        connect_args: dict[str, Any] = {
            "timeout": connect_timeout,
            "server_settings": {"statement_timeout": str(statement_timeout_ms)},
        }
        engine = create_async_engine(
            normalised,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            connect_args=connect_args,
        )
        _engines[normalised] = engine
        logger.info("Created async engine for %s", _mask_dsn(normalised))
        return engine


async def get_app_engine(settings: CoreSettings | None = None) -> AsyncEngine:
    """Build (or reuse) the engine from the bound ``CoreSettings.db_dsn``.

    Convenience wrapper that pulls all pool/timeout knobs from settings
    so application startup code stays a one-liner.

    Args:
        settings: Optional override; defaults to the bound runtime
            settings (``get_settings()``).

    Returns:
        The shared application engine.

    Raises:
        RuntimeError: ``db_dsn`` is unset on the resolved settings.
    """
    s = settings or get_settings()
    if not s.db_dsn:
        raise RuntimeError("CoreSettings.db_dsn is not set.")
    return await get_async_engine(
        s.db_dsn,
        pool_size=s.db_pool_size,
        max_overflow=s.db_pool_max_overflow,
        pool_pre_ping=s.db_pool_pre_ping,
        connect_timeout=s.db_connect_timeout,
        statement_timeout_ms=s.db_statement_timeout_ms,
    )


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an ``async_sessionmaker`` configured for ``engine``.

    ``expire_on_commit=False`` so models stay usable after commit —
    avoids surprise reloads in request handlers that emit a response
    after the transaction closes.

    Args:
        engine: Async SQLAlchemy engine.

    Returns:
        A reusable sessionmaker bound to ``engine``.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_all_engines() -> None:
    """Dispose every cached engine. Call from application lifespan shutdown."""
    async with _engine_lock:
        engines = list(_engines.items())
        _engines.clear()

    for dsn, engine in engines:
        try:
            await engine.dispose()
            logger.info("Disposed async engine for %s", _mask_dsn(dsn))
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to dispose engine for %s", _mask_dsn(dsn), exc_info=True
            )
