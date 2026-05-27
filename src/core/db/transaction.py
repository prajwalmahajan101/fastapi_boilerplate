"""Single transaction boundary helper for request-scoped sessions.

``session.begin()`` cannot be used on a session that already
autobegan a transaction — which is the state of any session that has
already issued a ``SELECT`` before reaching the handler (e.g. an auth or
tenant-lookup dependency sharing the request session), because
SQLAlchemy 2.x autobegins on the first ``execute``.

``atomic`` uses explicit ``commit`` / ``rollback`` so it works
regardless of whether the session is already in a transaction,
and keeps handlers free of per-call try/except/commit/rollback
boilerplate. Use it everywhere ``async with session.begin():``
would otherwise appear.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def atomic(session: AsyncSession) -> AsyncIterator[None]:
    """Commit on clean exit, roll back on any exception.

    Equivalent to ``async with session.begin():`` for a fresh
    session, but tolerant of a pre-existing autobegun transaction
    on ``session``.

    Args:
        session: ``AsyncSession`` to commit / roll back.

    Yields:
        ``None`` — the caller runs its work inside the block.

    Raises:
        Exception: Re-raises whatever the wrapped block raises,
            after rolling back ``session``.
    """
    try:
        yield
        await session.commit()
    except Exception:
        await session.rollback()
        raise


__all__ = ["atomic"]
