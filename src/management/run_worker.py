"""Celery worker / beat entrypoint.

Run with::

    python -m src.management.run_worker worker
    python -m src.management.run_worker beat
    python -m src.management.run_worker worker -Q email,default --concurrency 4

The script forwards every remaining argv token to Celery's CLI so any
``celery worker`` / ``celery beat`` flag works as documented. Tasks are
imported from ``src.tasks`` (autodiscover entry below) so domain task
modules registered via :func:`src.core.tasks.register_task` are visible
to the worker without per-module wiring here.
"""

from __future__ import annotations

import logging
import sys

from src.common.settings import settings  # noqa: F401  — eager validation at boot
from src.core.runtime import configure
from src.core.tasks import celery_app
from src.core.utils.logging import setup_logging

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Bind settings, configure logging, and hand off to the Celery CLI.

    Args:
        argv: Optional argv override (defaults to ``sys.argv[1:]``).

    Returns:
        The Celery CLI exit code.
    """
    setup_logging()
    configure(settings)

    # Autodiscover task modules. The convention is one module per
    # domain area (e.g. ``src.tasks.email``); the worker imports them
    # so ``register_task`` / ``async_task`` decorators populate the
    # Celery registry before jobs are pulled off the queue.
    celery_app.autodiscover_tasks(["src.tasks"], force=True)

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["worker"]
    logger.info("Starting Celery: %s", " ".join(args))
    return celery_app.start(argv=args)


if __name__ == "__main__":
    raise SystemExit(main())
