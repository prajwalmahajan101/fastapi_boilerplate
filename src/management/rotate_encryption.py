"""Re-encrypt every ``EncryptedString`` column onto the current primary key.

Field-level encryption uses ``resilience_kit.crypto.FernetCipher``, which is
backed by ``MultiFernet`` as of resilience-kit 0.2.0: the *primary*
(first) key in ``RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS`` encrypts, and
every configured key is tried on decrypt. Key rotation is therefore a
three-step operator flow (see ``docs/key-rotation.md``):

    1. Prepend the new key:  ``[K_new, K_old]``  → new writes use ``K_new``,
       existing ciphertext under ``K_old`` still decrypts.
    2. Run **this command** to re-encrypt all stored ciphertext onto
       ``K_new`` without ever exposing plaintext.
    3. Drop the retired key:  ``[K_new]``.

This walks the raw ciphertext at the SQL layer (bypassing the
``EncryptedString`` type decorator) and calls
:meth:`FernetCipher.rotate`, so plaintext is never materialised in the
process. The sweep is **batched**: each table is walked by keyset
pagination on its primary key and every batch commits in its own short
transaction, so a large encrypted table does not hold a table-long write
transaction that would block concurrent writers to the auth hot path. A
token that cannot be decrypted aborts the run — the current batch rolls
back, but batches committed earlier stay rotated (the command is
idempotent, so re-run after fixing the key list).

Safe to re-run: Fernet tokens carry a timestamp + IV, so each run writes
fresh ciphertext even for already-primary rows — harmless, the plaintext
is preserved. Use ``--dry-run`` to count affected rows without writing (a
read-only pass — no transaction is opened for writes).

Usage::

    python -m src.management.rotate_encryption            # rotate + commit
    python -m src.management.rotate_encryption --dry-run   # report only
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from resilience_kit import FernetCipher
from resilience_kit.exceptions import DecryptionError
from src.common.settings import settings
from src.core.runtime import configure
from src.core.utils.db import get_app_engine

logger = logging.getLogger(__name__)

#: Registry of encrypted columns to sweep. Add a ``(table, pk, columns)``
#: tuple here when a new ``EncryptedString`` column lands so the rotation
#: command covers it. Names are in-tree constants (never user input), so
#: interpolating them into the DDL-free ``text()`` statements is safe.
ENCRYPTED_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("api_keys", "id", ("secret",)),
)


#: Rows read (and, when writing, updated) per batch. Bounds how much a
#: single transaction locks and how much ciphertext is materialised at
#: once, so a large encrypted table rotates without a table-long write
#: transaction blocking concurrent writers to the auth hot path.
_ROTATE_BATCH_SIZE = 500


async def rotate_all(*, dry_run: bool = False) -> int:
    """Re-encrypt every registered encrypted column onto the primary key.

    The sweep is **batched and resumable**, not a single transaction: each
    table is walked by keyset pagination on its primary key and each batch
    of re-encrypted rows commits in its own short transaction, so locks are
    released between batches instead of held for the whole table.

    Args:
        dry_run: When ``True``, decrypt-probe and count rows that would be
            rewritten over a read-only connection — no writes are issued.

    Returns:
        The number of column values rotated (or that would be rotated in
        a dry run).

    Raises:
        DecryptionError: A stored token is not decryptable under any
            configured key. The current batch's write transaction rolls
            back, but batches committed earlier in the sweep stay rotated.
            The command is idempotent, so fix the key list and re-run — the
            already-rotated rows re-encrypt harmlessly.
    """
    engine = await get_app_engine()
    rotated = 0
    try:
        for table, pk, columns in ENCRYPTED_COLUMNS:
            rotated += await _rotate_table(
                engine, table=table, pk=pk, columns=columns, dry_run=dry_run
            )
    finally:
        await engine.dispose()
    verb = "would rotate" if dry_run else "rotated"
    logger.info("Key rotation complete: %s %d encrypted value(s).", verb, rotated)
    return rotated


async def _rotate_table(
    engine: AsyncEngine,
    *,
    table: str,
    pk: str,
    columns: tuple[str, ...],
    dry_run: bool,
) -> int:
    """Rotate one table's encrypted columns via keyset-paginated batches.

    Args:
        engine: The application engine (kept open by the caller).
        table: Table name (in-tree constant — safe to interpolate).
        pk: Primary-key column used as the keyset cursor.
        columns: Encrypted column names to re-encrypt.
        dry_run: When ``True``, read and count only — no writes.

    Returns:
        The number of column values rotated (or that would be, dry-run).

    Raises:
        DecryptionError: Propagated from :meth:`FernetCipher.rotate` when a
            stored token cannot be decrypted under the configured keys.
    """
    col_list = ", ".join(columns)
    rotated = 0
    last_pk: object | None = None
    while True:
        # Read a batch over a read-only connection so the scan never holds
        # a write transaction open across the (CPU-bound) rotate step.
        async with engine.connect() as conn:
            if last_pk is None:
                stmt = text(
                    f"SELECT {pk}, {col_list} FROM {table} "
                    f"ORDER BY {pk} LIMIT :limit"
                )
                params: dict[str, object] = {"limit": _ROTATE_BATCH_SIZE}
            else:
                stmt = text(
                    f"SELECT {pk}, {col_list} FROM {table} "
                    f"WHERE {pk} > :last ORDER BY {pk} LIMIT :limit"
                )
                params = {"last": last_pk, "limit": _ROTATE_BATCH_SIZE}
            rows = (await conn.execute(stmt, params)).mappings().all()

        if not rows:
            break

        # Re-encrypt in memory (plaintext is never materialised).
        updates: list[tuple[str, str, object]] = []
        for row in rows:
            for col in columns:
                token = row[col]
                if not token:  # None / empty — stored unencrypted
                    continue
                try:
                    new_token = FernetCipher.rotate(token)
                except DecryptionError:
                    logger.error(
                        "Undecryptable token in %s.%s (%s=%s) — aborting; "
                        "check RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS "
                        "includes the key that wrote it.",
                        table,
                        col,
                        pk,
                        row[pk],
                    )
                    raise
                rotated += 1
                updates.append((col, new_token, row[pk]))
        last_pk = rows[-1][pk]

        # Commit this batch's writes in its own short transaction; locks
        # are released before the next batch is read.
        if not dry_run and updates:
            async with engine.begin() as conn:
                for col, new_token, row_pk in updates:
                    await conn.execute(
                        text(f"UPDATE {table} SET {col} = :val WHERE {pk} = :pk"),
                        {"val": new_token, "pk": row_pk},
                    )

        if len(rows) < _ROTATE_BATCH_SIZE:
            break
    return rotated


def main() -> None:
    """CLI entry point for the field-encryption key-rotation sweep."""
    parser = argparse.ArgumentParser(
        description="Re-encrypt all EncryptedString columns onto the primary "
        "Fernet key (resilience-kit MultiFernet rotation)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be rotated without writing.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    # The CLI runs outside the FastAPI lifespan, so bind the runtime
    # settings before any ``src.core.*`` settings read (see
    # ``src/management/CLAUDE.md``).
    configure(settings)
    asyncio.run(rotate_all(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
