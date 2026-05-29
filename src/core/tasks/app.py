"""Celery application instance — broker + backend bound to CoreSettings.

The Celery app is constructed at import time (Celery's API expects a
module-level instance the worker CLI can discover). Configuration is
read from ``CoreSettings`` via ``core.runtime.get_settings()`` so the
app inherits the same env-driven config as the HTTP service.

Broker / backend resolution
---------------------------

Both broker and result-backend default to the Redis URL named by
:data:`CoreSettings.task_redis_alias`. A deployment that wants to keep
result storage in Postgres or skip results entirely can override via
``CELERY_RESULT_BACKEND``. The broker URL is always Redis in this
boilerplate; swap it at the settings layer if you ever need RabbitMQ.

Why a separate sync runtime
---------------------------

Celery's prefork workers are sync processes — tasks are plain
functions. Async code (repositories, HTTP client, cache) can still run
inside a task via :func:`src.core.tasks.registry.async_task`, which
wraps a coroutine in ``asyncio.run`` per invocation. That isolation
keeps the worker free of FastAPI's lifespan and lets it scale on
process-level concurrency independent of the API.
"""

from __future__ import annotations

import logging

from celery import Celery

from src.core.runtime import get_settings

logger = logging.getLogger(__name__)


def _build_celery_app() -> Celery:
    """Construct the module-level Celery app.

    Returns:
        A configured :class:`celery.Celery` instance. Task modules are
        autodiscovered when the worker starts; producers can also
        register tasks at import time via
        :func:`src.core.tasks.registry.register_task`.
    """
    settings = get_settings()
    alias = settings.task_redis_alias
    if alias not in settings.redis_urls:
        raise KeyError(
            f"task_redis_alias={alias!r} is not in CoreSettings.redis_urls "
            f"(known: {sorted(settings.redis_urls)})."
        )
    broker_url = settings.redis_urls[alias]
    backend_url = settings.celery_result_backend or broker_url

    # Celery app name is used in logs / inspect output; keep it static
    # rather than reading from the ``src.common.Settings`` subclass —
    # ``src.core`` must not import ``src.common`` (one-way layering
    # rule enforced by ``scripts/check_layering.py``).
    app = Celery(
        "fastapi_boilerplate",
        broker=broker_url,
        backend=backend_url,
    )
    app.conf.update(
        task_default_queue=settings.task_queue_name,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_max_retries=settings.task_max_tries,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
    )
    logger.info(
        "Celery app configured (broker=%s, queue=%s)",
        alias,
        settings.task_queue_name,
    )
    return app


celery_app: Celery = _build_celery_app()


__all__ = ["celery_app"]
