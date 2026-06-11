"""Integration coverage for ``PostgresApiLogRepository``.

Exercises the batched drain path against a real Postgres ``api_logs``
table — bypassing the HTTP layer so we can assert column-level
serialisation, on-conflict idempotency, and queue-overflow back-pressure
without dragging the full app into every test.

The drain task uses a short ``flush_interval_s`` so tests don't pay the
default 1-second batch window. Each test starts a fresh repository,
seeds rows, awaits one flush cycle, queries ``api_logs`` directly, and
tears the repository down — leaving the shared ``pg_engine`` intact.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

from src.core.api_log.backends.postgres import PostgresApiLogRepository
from src.core.api_log.models import ApiLog, RequestDirection
from src.core.api_log.table import api_logs


def _log(
    *,
    log_id: str | None = None,
    direction: RequestDirection = RequestDirection.INBOUND,
    service_name: str = "api_log_e2e",
    url: str = "/api/v1/hello",
    method: str = "GET",
    response_status_code: int = 200,
    request_id: str | None = None,
    request_headers: dict[str, str] | None = None,
    request_body: str | None = None,
    response_headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> ApiLog:
    """Build a populated ``ApiLog`` with sensible defaults for these tests."""
    return ApiLog(
        log_id=log_id or str(uuid.uuid4()),
        direction=direction,
        service_name=service_name,
        request_id=request_id or str(uuid.uuid4()),
        environment="test",
        method=method,
        url=url,
        query_params={"q": "ada"},
        request_headers=request_headers or {"x-forwarded-for": "127.0.0.1"},
        request_body=request_body,
        response_status_code=response_status_code,
        response_headers=response_headers or {"content-type": "application/json"},
        response_body=None,
        duration_ms=12.5,
        timestamp=datetime.now(UTC),
        extra=extra,
    )


@pytest.fixture
async def repo(pg_engine) -> AsyncIterator[PostgresApiLogRepository]:
    """Start a short-interval repository and tear it down after each test."""
    instance = PostgresApiLogRepository(
        pg_engine, batch_size=10, flush_interval_s=0.05, queue_size=50
    )
    await instance.startup()
    try:
        yield instance
    finally:
        await instance.shutdown()
        # Wipe the rows this test inserted so the suite stays idempotent.
        async with pg_engine.begin() as conn:
            await conn.execute(
                delete(api_logs).where(api_logs.c.service_name == "api_log_e2e")
            )


async def _wait_for_rows(pg_engine, *, log_ids: list[str], timeout_s: float = 2.0):
    """Poll ``api_logs`` until every ``log_id`` lands, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        async with pg_engine.connect() as conn:
            result = await conn.execute(
                select(api_logs).where(api_logs.c.log_id.in_(log_ids))
            )
            rows = result.mappings().all()
        if len(rows) == len(log_ids):
            return rows
        if asyncio.get_event_loop().time() >= deadline:
            return rows
        await asyncio.sleep(0.05)


# ── basic persistence ───────────────────────────────────────────────


async def test_save_persists_row_with_all_columns(
    repo: PostgresApiLogRepository, pg_engine
) -> None:
    """A single ``save`` lands as one row with every column populated."""
    log = _log(request_body='{"name":"ada"}')
    await repo.save(log)

    rows = await _wait_for_rows(pg_engine, log_ids=[log.log_id])
    assert len(rows) == 1
    row = rows[0]
    assert row["log_id"] == log.log_id
    assert row["direction"] == RequestDirection.INBOUND.value
    assert row["service_name"] == "api_log_e2e"
    assert row["method"] == "GET"
    assert row["url"] == "/api/v1/hello"
    assert row["response_status_code"] == 200
    assert row["query_params"] == {"q": "ada"}
    assert row["request_headers"] == {"x-forwarded-for": "127.0.0.1"}
    assert row["response_headers"] == {"content-type": "application/json"}
    assert row["request_body"] == '{"name":"ada"}'
    assert row["duration_ms"] == pytest.approx(12.5)
    assert row["environment"] == "test"
    assert row["timestamp"] is not None


async def test_batch_drain_collapses_into_one_insert(
    repo: PostgresApiLogRepository, pg_engine
) -> None:
    """Multiple back-to-back saves drain in a single batch and all land."""
    logs = [_log() for _ in range(7)]
    for log in logs:
        await repo.save(log)

    rows = await _wait_for_rows(pg_engine, log_ids=[log.log_id for log in logs])
    assert {row["log_id"] for row in rows} == {log.log_id for log in logs}


# ── idempotency ─────────────────────────────────────────────────────


async def test_duplicate_log_id_is_idempotent(
    repo: PostgresApiLogRepository, pg_engine
) -> None:
    """Re-saving the same ``log_id`` is silently dropped (ON CONFLICT DO NOTHING)."""
    log = _log()
    await repo.save(log)
    rows = await _wait_for_rows(pg_engine, log_ids=[log.log_id])
    assert len(rows) == 1
    original_id = rows[0]["id"]

    # Second save under the same log_id — body changed to prove the
    # first write wins (no UPDATE happens on conflict).
    duplicate = _log(log_id=log.log_id, request_body="should-be-ignored")
    await repo.save(duplicate)
    # Give the drain a moment so the duplicate is processed.
    await asyncio.sleep(0.2)

    async with pg_engine.connect() as conn:
        result = await conn.execute(
            select(api_logs).where(api_logs.c.log_id == log.log_id)
        )
        all_rows = result.mappings().all()
    assert len(all_rows) == 1
    assert all_rows[0]["id"] == original_id
    assert all_rows[0]["request_body"] is None


# ── outbound direction ──────────────────────────────────────────────


async def test_outbound_direction_recorded(
    repo: PostgresApiLogRepository, pg_engine
) -> None:
    """Outbound logs persist with ``direction='OUTBOUND'`` distinct from inbound."""
    log = _log(direction=RequestDirection.OUTBOUND, url="https://api.example.com/v1")
    await repo.save(log)

    rows = await _wait_for_rows(pg_engine, log_ids=[log.log_id])
    assert rows[0]["direction"] == RequestDirection.OUTBOUND.value
    assert rows[0]["url"] == "https://api.example.com/v1"


# ── queue overflow ──────────────────────────────────────────────────


async def test_queue_overflow_drops_with_warning(
    pg_engine, caplog: pytest.LogCaptureFixture
) -> None:
    """When the buffer is saturated newer rows are dropped, never raised.

    Uses a freshly-built repository *without* starting the drain task
    so we can deterministically saturate the queue. A started drain
    would race the producer.
    """
    instance = PostgresApiLogRepository(
        pg_engine, batch_size=10, flush_interval_s=0.05, queue_size=3
    )
    # Note: no startup() — drain task stays asleep so the queue fills.
    try:
        with caplog.at_level(
            logging.WARNING, logger="src.core.api_log.backends.postgres"
        ):
            for _ in range(5):  # 2 over capacity
                await instance.save(_log())
        # Three slots accepted, two warnings emitted.
        warnings = [
            r
            for r in caplog.records
            if "queue full" in r.getMessage()
            and r.name == "src.core.api_log.backends.postgres"
        ]
        assert len(warnings) == 2
    finally:
        # No drain task was started, so just drop the buffered rows.
        instance._stopping.set()  # noqa: SLF001
