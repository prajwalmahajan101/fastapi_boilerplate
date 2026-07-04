"""Integration test for the batched field-encryption key-rotation sweep.

Exercises ``src.management.rotate_encryption._rotate_table`` against a real
Postgres engine (``pg_engine``, auto-skips when Postgres is unreachable).
The batch size is shrunk to 2 so a handful of seeded rows spans multiple
keyset-paginated batches — covering the ISSUE-041/042 fix:

* every row is re-encrypted across batch boundaries (multi-batch keyset walk);
* ``--dry-run`` opens no write transaction and mutates nothing;
* a real rotation rewrites the ciphertext while preserving the plaintext.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.management import rotate_encryption as rot

_TABLE = "rotate_test_secrets"
_PLAINTEXTS = ["alpha", "bravo", "charlie", "delta", "echo"]  # 5 rows → 3 batches at size 2


@pytest.fixture
def _fernet_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure a real Fernet key for the kit and reset its cache.

    The kit's ``FernetCipher`` reads its keys from the environment at call
    time (lru-cached). Set a fresh single-key list, drop it into a non-prod
    crypto environment, and reset the cache before and after so the key does
    not leak into other tests.
    """
    from resilience_kit.crypto.fernet import reset_fernet_cache

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("RESILIENCE_CRYPTO__ENVIRONMENT", "test")
    monkeypatch.setenv("RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS", json.dumps([key]))
    reset_fernet_cache()
    try:
        yield
    finally:
        reset_fernet_cache()


@pytest.fixture
async def _seeded_table(
    pg_engine: AsyncEngine, _fernet_key: None
) -> AsyncIterator[list[str]]:
    """Create + seed a temp table of encrypted secrets; drop it on teardown.

    Yields the list of ciphertexts as originally written, so the test can
    assert they change after rotation.
    """
    from resilience_kit import FernetCipher

    ciphertexts = [FernetCipher.encrypt(p) for p in _PLAINTEXTS]
    async with pg_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))
        await conn.execute(
            text(f"CREATE TABLE {_TABLE} (id SERIAL PRIMARY KEY, secret TEXT)")
        )
        for ct in ciphertexts:
            await conn.execute(
                text(f"INSERT INTO {_TABLE} (secret) VALUES (:s)"), {"s": ct}
            )
    try:
        yield ciphertexts
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))


async def _read_secrets(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text(f"SELECT secret FROM {_TABLE} ORDER BY id"))
        ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_dry_run_counts_without_writing(
    pg_engine: AsyncEngine,
    _seeded_table: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run reports every row but leaves the ciphertext untouched."""
    monkeypatch.setattr(rot, "_ROTATE_BATCH_SIZE", 2)

    count = await rot._rotate_table(
        pg_engine, table=_TABLE, pk="id", columns=("secret",), dry_run=True
    )

    assert count == len(_PLAINTEXTS)
    assert await _read_secrets(pg_engine) == _seeded_table  # unchanged


@pytest.mark.asyncio
async def test_rotation_rewrites_all_rows_across_batches(
    pg_engine: AsyncEngine,
    _seeded_table: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every row is rotated across multiple keyset batches; plaintext preserved."""
    from resilience_kit import FernetCipher

    monkeypatch.setattr(rot, "_ROTATE_BATCH_SIZE", 2)

    count = await rot._rotate_table(
        pg_engine, table=_TABLE, pk="id", columns=("secret",), dry_run=False
    )

    assert count == len(_PLAINTEXTS)
    rotated = await _read_secrets(pg_engine)
    # Ciphertext is fresh (new timestamp/IV) for every row...
    assert all(new != old for new, old in zip(rotated, _seeded_table, strict=True))
    # ...but decrypts back to the original plaintext.
    assert [FernetCipher.decrypt(c) for c in rotated] == _PLAINTEXTS
