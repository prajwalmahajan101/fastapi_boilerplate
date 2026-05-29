"""FastAPI application factory + lifespan.

Lifespan order on startup:
    1. ``configure(settings)`` — bind the concrete ``Settings`` to
       ``core.runtime`` so core code can read config without importing
       ``src.common`` (the dependency rule forbids that direction).
    2. ``wait_for_redis`` — give Redis a short window to come up before
       any resilience provider is first called (see the comment below).
    3. ``init_db_engine`` — build/cache the shared application engine.
    4. ``init_repository`` — start the api_log Postgres/Noop audit backend.

Shutdown reverses the order, then drains pending fire-and-forget log
tasks, closes the shared HTTP client + Redis clients, and disposes the
engine cache.

Exception → HTTP status mappings are registered centrally in
``src/core/exceptions/handlers.py``. Project-specific exception families
register their own mapping there (or at startup) — no change to this file
is needed to add one.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.router import root_router
from src.common.openapi_metadata import API_DESCRIPTION, TAGS_METADATA
from src.common.settings import settings
from src.core.api_log import close_repository, init_repository
from src.core.exceptions import register_exception_handlers
from src.core.middleware import install_core_middleware
from src.core.resilience.recovery import monitor as recovery_monitor
from src.core.runtime import configure
from src.core.utils.crypto import _fernet
from src.core.utils.fire_and_forget import drain_all
from src.core.utils.http_client import AsyncAPIClient
from src.core.utils.logging import setup_logging
from src.core.utils.redis import close_all_redis_clients, wait_for_redis
from src.db import close_db_engine, init_db_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup / shutdown.

    Args:
        app: The FastAPI app being started.

    Yields:
        Control while the app serves requests.
    """
    configure(settings)
    # Probe the field-encryption key once at boot. A missing or broken
    # FERNET configuration would otherwise raise on the first encrypt /
    # decrypt request and present as a 500 to the caller; failing the
    # lifespan keeps the process out of the load balancer instead.
    _fernet()
    # Give Redis a short window to come up before any resilience provider
    # is first called. Once a provider cached an in-memory backend (because
    # the very first ping failed), no probe can rebuild it — a boot-time
    # outage would be one-way until restart. The retry budget is short:
    # if Redis is genuinely down, the subsystem degrades to in-memory with
    # a clear warning trail.
    for alias in {
        settings.circuit_breaker_redis_alias,
        settings.rate_limit_redis_alias,
        "default",  # cache backend's default alias
    }:
        if not await wait_for_redis(alias):
            logger.warning(
                "Redis alias '%s' unreachable after retries; resilience "
                "subsystem will boot on the in-memory fallback (recovers "
                "only via process restart for that alias).",
                alias,
            )
    await init_db_engine()
    await init_repository()
    # Start the background recovery monitor *after* the resilience
    # providers' boot-time PINGs have happened (during their first
    # lazy access) so it inherits an accurate boot-fallback alias set.
    # Started here rather than at module import so tests don't spawn
    # rogue tasks when they import the app.
    recovery_monitor.start()
    logger.info("Application startup complete.")
    try:
        yield
    finally:
        await recovery_monitor.stop()
        await close_repository()
        await drain_all(settings.api_log_drain_timeout_seconds)
        await AsyncAPIClient.close_session()
        await close_all_redis_clients()
        await close_db_engine()
        logger.info("Application shutdown complete.")


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Configures structured logging *before* anything else so module-level
    ``logger`` calls during settings load / engine init land in the
    configured stream (formatted, request-id aware) instead of stdlib
    defaults.

    Returns:
        Configured ``FastAPI`` instance.
    """
    setup_logging()

    # ``/docs``, ``/redoc``, and ``/openapi.json`` ship off by default.
    # Dev / staging opts in via ``OPENAPI_DOCS_ENABLED=true``. Passing
    # ``None`` disables the route entirely; the relaxed docs-path CSP in
    # ``SecurityHeadersMiddleware`` is then inert.
    docs_url = "/docs" if settings.openapi_docs_enabled else None
    redoc_url = "/redoc" if settings.openapi_docs_enabled else None
    openapi_url = "/openapi.json" if settings.openapi_docs_enabled else None

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    install_core_middleware(
        app,
        cors_enabled=settings.cors_enabled,
        cors_excluded_prefixes=settings.cors_excluded_prefixes,
        cors_allow_origins=settings.cors_allow_origins,
        cors_allow_methods=settings.cors_allow_methods,
        cors_allow_headers=settings.cors_allow_headers,
        cors_allow_credentials=settings.cors_allow_credentials,
        enable_security_headers=settings.security_headers_enabled,
        enable_metrics_middleware=settings.metrics_middleware_enabled,
    )

    register_exception_handlers(app)
    app.include_router(root_router)
    return app


app = create_app()
