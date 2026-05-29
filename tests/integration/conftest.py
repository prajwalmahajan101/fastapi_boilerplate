"""Integration-tier conftest.

Integration tests exercise **one layer** against a real backing store —
either Postgres (repositories, ORM, alembic-shaped queries) or Redis
(throttle, cache, circuit-breaker). They do **not** drive the full HTTP
request path (that is the e2e tier).

Fixtures here probe the local services and ``pytest.skip`` cleanly when
they are not reachable, so the tier is safe to run on a workstation that
hasn't booted ``docker compose`` yet — the relevant tests are skipped
rather than erroring.

Bring services up locally with::

    docker compose up postgres redis db-init -d
    pytest -m integration

Add fixtures here that span the tier (DSN probes, engine/session,
redis client). Per-area fixtures belong in a local ``conftest.py``.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator, Iterator

import pytest


def _tcp_reachable(host: str, port: int, timeout: float = 0.25) -> bool:
    """Return ``True`` when ``(host, port)`` accepts a TCP connection.

    Args:
        host: Hostname or IP to probe.
        port: TCP port to probe.
        timeout: Per-attempt socket timeout in seconds.

    Returns:
        ``True`` if a connection succeeded, ``False`` otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Return the Postgres DSN used by the integration tier.

    Skips the test when the database is not reachable so the suite
    stays green on a workstation that hasn't started the compose
    stack yet.

    Returns:
        A SQLAlchemy-style async DSN suitable for ``create_async_engine``.
    """
    dsn = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
    )
    host = dsn.rsplit("@", 1)[-1].split("/", 1)[0]
    hostname, _, port = host.partition(":")
    if not _tcp_reachable(hostname or "localhost", int(port or 5432)):
        pytest.skip("Postgres not reachable — run `docker compose up postgres -d`.")
    return dsn


@pytest.fixture(scope="session")
def redis_url() -> str:
    """Return the Redis URL used by the integration tier.

    Skips when Redis is unreachable, same rationale as :func:`pg_dsn`.

    Returns:
        A ``redis://host:port/db`` URL.
    """
    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
    host_part = url.split("://", 1)[-1].split("/", 1)[0]
    hostname, _, port = host_part.partition(":")
    if not _tcp_reachable(hostname or "localhost", int(port or 6379)):
        pytest.skip("Redis not reachable — run `docker compose up redis -d`.")
    return url


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[object]:
    """Yield a flushed ``redis.asyncio.Redis`` client for the test.

    The client connects to :func:`redis_url`, flushes the selected db
    before the test, yields, and closes on teardown — so each test
    sees a clean keyspace.

    Args:
        redis_url: The integration-tier Redis URL fixture.

    Yields:
        An async Redis client.
    """
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def pg_engine(pg_dsn: str) -> Iterator[object]:
    """Yield a session-scoped async SQLAlchemy engine bound to ``pg_dsn``.

    The engine is disposed on teardown so connection-pool slots don't
    leak between tests.

    Args:
        pg_dsn: The integration-tier Postgres DSN fixture.

    Yields:
        An ``AsyncEngine`` bound to the test database.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_dsn, future=True)
    try:
        yield engine
    finally:
        import asyncio

        asyncio.get_event_loop().run_until_complete(engine.dispose())
