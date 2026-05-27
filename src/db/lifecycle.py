"""Thin wrappers around the core engine cache for FastAPI lifespan use.

``init_db_engine`` returns the shared application ``AsyncEngine`` (built
once, cached by DSN — the same engine used by ``api_log`` and every
request-scoped session). ``close_db_engine`` is the paired shutdown hook;
it delegates to
``core.utils.db.dispose_all_engines`` so the lifespan code reads as a
symmetric ``init_db_engine`` / ``close_db_engine`` pair.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from src.common.settings import settings
from src.core.utils.db import dispose_all_engines, get_app_engine


async def init_db_engine() -> AsyncEngine:
    """Return the shared application engine, creating it on first call.

    Returns:
        The async engine bound to ``settings.db_dsn``.
    """
    return await get_app_engine(settings)


async def close_db_engine() -> None:
    """Dispose every cached engine — paired with :func:`init_db_engine`.

    Delegates to ``core.utils.db.dispose_all_engines`` so the lifespan
    has one explicit shutdown call instead of reaching into core utils.
    """
    await dispose_all_engines()
