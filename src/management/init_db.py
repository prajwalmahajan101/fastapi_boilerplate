"""DEPRECATED one-shot DDL bootstrap — kept as an emergency fallback only.

Alembic is now the canonical schema source (``alembic upgrade head``).
This script's ``metadata.create_all`` is idempotent and harmless to run
against an Alembic-managed database, but it does **not** stamp Alembic's
``alembic_version`` table — so running it on a fresh DB will leave
Alembic thinking the schema is unversioned. Use only when Alembic is
unavailable (e.g. a stripped-down recovery image), and follow up with
``alembic stamp head`` once Alembic is reachable again.
"""

from __future__ import annotations

import asyncio
import logging

from src.common.settings import settings  # noqa: F401  — eager validation at boot
from src.core.api_log.table import metadata as api_log_metadata
from src.core.base.model import BaseModel
from src.core.utils.db import get_app_engine
from src.db import tables  # noqa: F401  — registers models on BaseModel.metadata

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Create every known table on the configured database."""
    engine = await get_app_engine()
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
        await conn.run_sync(api_log_metadata.create_all)
    logger.info("Database schema synchronised.")


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(init_db())


if __name__ == "__main__":
    main()
