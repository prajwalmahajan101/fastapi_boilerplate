"""``best_effort_atomic`` — best-effort transactional write helper.

Wraps :func:`atomic` with a ``try / except`` that **logs and swallows**
any exception, so a caller can fire off audit / tracking / outcome writes
that **must not fail the operation that preceded them**. This is the
shape every "best-effort tracking" write in the codebase used to
hand-roll; the helper exists so the intent is named, not just
duplicated.

Use this only for writes whose failure mode is "log and move on" — e.g.
normalized tracking rollups, audit fan-out, last-used stamps. Never for
the authoritative write the caller's response depends on.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db.transaction import atomic


@asynccontextmanager
async def best_effort_atomic(
    session: AsyncSession,
    label: str,
    *,
    logger: logging.Logger,
) -> AsyncIterator[None]:
    """Run a transactional block; log + swallow any exception.

    Equivalent to::

        try:
            async with atomic(session):
                ...
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("failed to <label>", exc_info=True)

    but the intent is named — readers see "best-effort" instead of
    reconstructing it from the bare try/except.

    Args:
        session: Request-scoped (or short-lived background)
            ``AsyncSession`` to commit / roll back.
        label: Short, action-oriented identifier folded into the warning
            log line (e.g. ``"record push_case outcome for APP1"``). The
            log line is ``"failed to %s"`` so the label should read as a
            verb phrase.
        logger: The caller's module logger. Passed in (not module-level
            here) so log lines carry the caller's module name, which is
            what operators filter on.

    Yields:
        ``None`` — the caller runs its work inside the block.
    """
    try:
        async with atomic(session):
            yield
    except Exception:  # noqa: BLE001 — best-effort writes must never fail the calling operation; logged for operators.
        logger.warning("failed to %s", label, exc_info=True)


__all__ = ["best_effort_atomic"]
