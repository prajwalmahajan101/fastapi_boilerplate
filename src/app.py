"""FastAPI application factory + lifespan.

The kit (``resilience-kit``) owns the resilience subsystem lifecycle —
its ``resilience_lifespan`` wraps the boilerplate's own lifespan,
starting the recovery monitor + audit dispatcher on enter and draining
both on exit. The boilerplate's lifespan keeps ownership of:

    1. ``configure(settings)`` — bind the concrete ``Settings`` to
       ``core.runtime`` so core code can read config without importing
       ``src.common`` (the dependency rule forbids that direction).
    2. ``wait_for_redis`` — give Redis a short window to come up before
       any resilience provider is first called.
    3. ``init_db_engine`` — build/cache the shared application engine.
    4. ``init_repository`` — start the api_log Postgres/Noop audit backend
       (deleted in a later commit when api_log migrates to the kit's audit).

Shutdown reverses the order, then closes the shared HTTP client +
Redis clients and disposes the engine cache.

Exception → HTTP status mappings: a single ``ResilienceKitError``
handler in ``src/core/exceptions/handlers.py`` bridges kit-raised
errors into the boilerplate ``ErrorEnvelope`` via
``resilience_kit.adapters._envelope.from_exception``. Project-domain
families (``BaseCustomError`` subclasses) keep their own handler on
the same ``app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from resilience_kit.adapters.fastapi import (
    install_middleware_stack,
    resilience_lifespan,
)

from src.api.router import root_router
import src.auth  # noqa: F401 — import-time side-effect wires RBAC's current-user hook
from src.common.openapi_metadata import API_DESCRIPTION, TAGS_METADATA
from src.common.settings import settings
from src.core.api_log import close_repository, init_repository
from src.core.exceptions import register_exception_handlers
from src.core.middleware.metrics_middleware import MetricsMiddleware
from src.core.middleware.request_id_bridge import RequestIdBridgeMiddleware
from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.core.runtime import configure
from src.core.utils.logging import setup_logging
from src.core.utils.redis import close_all_redis_clients, wait_for_redis
from src.db import close_db_engine, init_db_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boilerplate-owned startup/shutdown (wrapped by ``resilience_lifespan``).

    Args:
        app: The FastAPI app being started.

    Yields:
        Control while the app serves requests.
    """
    configure(settings)
    # The kit's ``FernetCipher`` is lazy + env-guarded: in ``prod`` it
    # refuses to build without ``RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEY``
    # set; dev / test fall back to an insecure default with a one-time
    # warning. The boot-time probe previously here is no longer needed —
    # if the key is unset the first encrypted-column read/write fails
    # with ``EncryptionConfigError`` and presents as a startup error.
    # Give Redis a short window to come up before any resilience provider
    # is first called. Once a provider cached an in-memory backend (because
    # the very first ping failed), no probe can rebuild it — a boot-time
    # outage would be one-way until restart. The retry budget is short:
    # if Redis is genuinely down, the subsystem degrades to in-memory with
    # a clear warning trail. The kit's recovery monitor takes over from
    # there on the next successful ping.
    for alias in {
        settings.rate_limit_redis_alias,
        "default",  # cache backend's default alias
    }:
        if not await wait_for_redis(alias):
            logger.warning(
                "Redis alias '%s' unreachable after retries; resilience "
                "subsystem will boot on the in-memory fallback (recovers "
                "via the kit's background monitor when the alias comes back).",
                alias,
            )
    await init_db_engine()
    await init_repository()
    logger.info("Application startup complete.")
    try:
        yield
    finally:
        await close_repository()
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
    # the kit's ``SecurityHeadersMiddleware`` is then inert.
    docs_url = "/docs" if settings.openapi_docs_enabled else None
    redoc_url = "/redoc" if settings.openapi_docs_enabled else None
    openapi_url = "/openapi.json" if settings.openapi_docs_enabled else None

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=resilience_lifespan(inner=_app_lifespan),
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # ``RequestIdBridgeMiddleware`` must be installed BEFORE the kit's
    # stack so Starlette's prepend semantics put it innermost — i.e. it
    # runs after the kit's RequestIDMiddleware has populated
    # ``resilience_kit.context.request_id``. The bridge then copies that
    # value into ``src.core.context.request_id_ctx`` for the request
    # lifetime so boilerplate-shaped envelopes / log lines / audit rows
    # see a non-null ``request_id``.
    app.add_middleware(RequestIdBridgeMiddleware)

    # Kit-owned middleware (six classes, innermost→outermost order
    # handled inside the installer). CORS is intentionally NOT installed
    # via the kit here: the kit's ``SelectiveCorsMiddleware`` is an
    # *allow-list* over path prefixes, while the boilerplate's deployment
    # model uses an *exclude-list* on top of FastAPI's standard
    # ``CORSMiddleware``. The two don't translate cleanly, so we keep the
    # vanilla Starlette CORS layer below and skip the kit's CORS layer.
    install_middleware_stack(
        app,
        body_limit_bytes=settings.max_request_body_bytes,
    )

    # Boilerplate-specific middleware. Order matters: ``add_middleware``
    # prepends, so the calls below become *outer* relative to the kit's
    # stack. Request logging sits outside the kit's ExceptionLogging so
    # every request — including those the kit rejects — is logged.
    app.add_middleware(RequestLoggingMiddleware)
    if settings.metrics_middleware_enabled:
        app.add_middleware(MetricsMiddleware)

    # Vanilla Starlette CORS (see install_middleware_stack comment above).
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins or ["*"],
            allow_methods=settings.cors_allow_methods or ["*"],
            allow_headers=settings.cors_allow_headers or ["*"],
            allow_credentials=settings.cors_allow_credentials,
        )

    # ``register_exception_handlers`` registers a single
    # ``ResilienceKitError`` handler that translates kit-raised errors
    # through ``from_exception`` into the boilerplate's ``ErrorEnvelope``
    # plus the existing handlers for boilerplate ``BaseCustomError``
    # subclasses, FastAPI ``RequestValidationError``, and the catch-all
    # 500. The kit's ``install_exception_handlers`` is intentionally NOT
    # called here — running both would emit two incompatible envelope
    # shapes for kit-raised vs project-raised errors.
    register_exception_handlers(app)

    app.include_router(root_router)
    return app


app = create_app()
