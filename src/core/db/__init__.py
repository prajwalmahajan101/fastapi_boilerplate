"""Request-scoped database dependencies for FastAPI routes.

Used to be: every handler reopened the engine + sessionmaker + session
itself, and the auth dependency opened a second session of its own. By
exposing ``get_session`` as a single FastAPI dependency, auth and the
handler share one session per request — both can read the same row
without traversing the connection pool twice.

``atomic`` is the transaction-boundary helper that handlers use in
place of ``async with session.begin():`` — it tolerates the autobegun
transaction left by the auth dependency's ``SELECT``.
"""

from src.core.db.dependencies import get_session
from src.core.db.transaction import atomic

__all__ = ["atomic", "get_session"]
