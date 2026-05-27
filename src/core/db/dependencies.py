"""FastAPI dependencies that yield an ``AsyncSession`` per request.

The sessionmaker is reused across requests (it caches the bound engine),
but each request gets its own ``AsyncSession`` from it — entered via
``async with`` so it is always closed on the way out. Write paths wrap
their work in ``async with atomic(session):`` (see
:func:`src.core.db.transaction.atomic`) — that helper commits on clean
exit and rolls back on exception, and tolerates the autobegun
transaction left by the auth dependency's ``SELECT``. Read-only
endpoints leave the transaction implicit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.utils.db import get_app_engine, get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped ``AsyncSession`` from the shared engine.

    FastAPI calls this for every request that ``Depends`` on it,
    opening and closing the session around the request lifecycle. Both
    the auth dependency and the route handler can take the same
    parameter and operate on the same session — keeping reads
    consistent and avoiding pool churn.

    Yields:
        An open ``AsyncSession`` bound to the application engine.
    """
    engine = await get_app_engine()
    SessionLocal = get_sessionmaker(engine)
    async with SessionLocal() as session:
        yield session


__all__ = ["get_session"]
