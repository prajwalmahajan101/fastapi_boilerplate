"""Health + readiness router factories.

Callers compose a list of async ``Check`` callables and pass them to
``create_health_router`` / ``create_readiness_router``. Pre-built checks
for the common backends (DB, Redis, Cache) live in this module.

A *health* endpoint typically answers "is the process alive?" (only the
DB check). A *readiness* endpoint answers "should the load balancer
route traffic here?" (DB + Redis + Cache + any external dependency).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core.context import get_request_id
from src.core.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of one health/readiness probe."""

    name: str
    healthy: bool
    detail: str = ""


Check = Callable[[], Awaitable[HealthCheckResult]]


def _envelope(
    *, status: str, healthy: bool, checks: list[HealthCheckResult]
) -> dict[str, Any]:
    """Compose the JSON body returned by every health / readiness probe.

    Args:
        status: Short human-readable label
            (``"healthy"`` / ``"ready"`` / their unhealthy counterparts).
        healthy: Whether every check passed.
        checks: Individual results to enumerate in the body.

    Returns:
        Dict with ``status``, ``healthy``, ``checks``, ``request_id``.
    """
    return {
        "status": status,
        "healthy": healthy,
        "checks": [
            {"name": c.name, "healthy": c.healthy, "detail": c.detail} for c in checks
        ],
        "request_id": get_request_id(),
    }


def create_health_router(
    *,
    checks: list[Check] | None = None,
    healthy_status: str = "healthy",
    unhealthy_status: str = "unhealthy",
    path: str = "/health",
) -> APIRouter:
    """Build a router exposing ``path`` — 200 when every check passes, 503 otherwise.

    Args:
        checks: Async probe callables; ``None`` is treated as ``[]``
            (always-healthy endpoint).
        healthy_status: ``status`` label when every check passes.
        unhealthy_status: ``status`` label when any check fails.
        path: URL path the probe is mounted at.

    Returns:
        A ``FastAPI.APIRouter`` ready to be included on the app.
    """
    router = APIRouter()
    check_callables = checks or []

    @router.get(path, include_in_schema=True)
    async def _health() -> JSONResponse:
        """Run every configured check and return the aggregated probe response.

        Returns:
            ``JSONResponse`` with status 200 / 503 and the envelope body.
        """
        results: list[HealthCheckResult] = []
        for check in check_callables:
            try:
                results.append(await check())
            except Exception as exc:  # noqa: BLE001
                logger.exception("Health check raised: %s", exc)
                results.append(
                    HealthCheckResult(
                        name=getattr(check, "__name__", "check"),
                        healthy=False,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
        healthy = all(r.healthy for r in results) if results else True
        status_text = healthy_status if healthy else unhealthy_status
        return JSONResponse(
            status_code=200 if healthy else 503,
            content=_envelope(status=status_text, healthy=healthy, checks=results),
        )

    return router


def create_readiness_router(
    *,
    checks: list[Check] | None = None,
    path: str = "/readiness",
) -> APIRouter:
    """Build a readiness router — same shape as :func:`create_health_router`.

    Args:
        checks: Async probe callables.
        path: URL path the probe is mounted at.

    Returns:
        ``APIRouter`` with status labels ``"ready"`` / ``"not_ready"``.
    """
    return create_health_router(
        checks=checks,
        healthy_status="ready",
        unhealthy_status="not_ready",
        path=path,
    )


# ── Pre-built probes ───────────────────────────────────────────────────


async def db_check() -> HealthCheckResult:
    """Probe the application database with ``SELECT 1``.

    Returns:
        ``HealthCheckResult`` named ``"database"`` with the connection
        outcome (the error is folded into ``detail`` on failure).
    """
    try:
        from sqlalchemy import text

        from src.core.utils.db import get_app_engine

        engine = await get_app_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return HealthCheckResult(name="database", healthy=True, detail="connected")
    except Exception as exc:  # noqa: BLE001
        return HealthCheckResult(
            name="database",
            healthy=False,
            detail=f"{type(exc).__name__}: {exc}",
        )


async def _redis_alive(alias: str) -> bool:
    """Best-effort PING against ``alias``'s Redis client.

    Used by the cache / throttle probes to decide whether a bare
    in-memory fallback (cached at boot because Redis was unreachable)
    should be torn down so the next provider call rebuilds against a
    now-live Redis.

    Args:
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        ``True`` on ``PING`` success; ``False`` on any failure.
    """
    try:
        from src.core.utils.redis import get_redis_client

        client = await get_redis_client(alias)
        await client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def cache_check(alias: str = "default") -> Check:
    """Build a probe that calls ``is_healthy`` on the named cache alias.

    When the currently-cached backend label is ``"memory"`` (boot-time
    fallback because Redis was unreachable when the cache was first
    constructed) the probe additionally pings the configured Redis
    alias directly. On success it calls
    :func:`cache.provider.reset_backend` so the next ``get_cache``
    rebuilds against Redis — closing the asymmetry between the
    in-call recovery probe (which can only promote an
    already-wrapped :class:`RedisCacheBackend`) and a bare
    :class:`InMemoryCacheBackend`.

    Args:
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        An async probe callable; the closure carries ``alias`` so each
        cache alias can be checked independently.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the cache backend, attempt boot-time recovery, then probe.

        Returns:
            ``HealthCheckResult`` named ``cache[<alias>]`` with the
            outcome; the backend label is exposed via ``detail``.
        """
        try:
            from src.core.resilience.cache.provider import get_cache, reset_backend

            cache = await get_cache(alias)
            if cache.backend_name == "memory" and await _redis_alive(alias):
                logger.info(
                    "Cache[%s] boot-fallback recovered (readyz probe); "
                    "rebuilding backend.",
                    alias,
                )
                await reset_backend(alias)
                cache = await get_cache(alias)
            healthy = await cache.is_healthy()
            return HealthCheckResult(
                name=f"cache[{alias}]",
                healthy=healthy,
                detail=cache.backend_name,
            )
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                name=f"cache[{alias}]",
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
            )

    _check.__name__ = f"cache_check[{alias}]"
    return _check


def throttle_check() -> Check:
    """Build a probe that calls ``is_healthy`` on the process-wide throttle.

    The throttle is a singleton (one backend serving every scope) so
    there is no alias to plumb — the throttle's own ``backend_name``
    is reported in ``detail`` for operator visibility. When the cached
    throttle is the bare :class:`InMemoryThrottle` (boot-time fallback)
    the probe pings the configured rate-limit alias and, on success,
    calls :func:`throttle.provider.reset_backend` so the next
    ``get_throttle`` call rebuilds against Redis.

    Returns:
        An async probe callable named ``throttle_check`` for logging.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the throttle backend, attempt boot-time recovery, then probe.

        Returns:
            ``HealthCheckResult`` named ``throttle`` with the outcome;
            the backend label is exposed via ``detail``.
        """
        try:
            from src.core.resilience.throttle.provider import (
                get_throttle,
                reset_backend,
            )
            from src.core.runtime import get_settings

            throttle = await get_throttle()
            if throttle.backend_name == "memory":
                alias = get_settings().rate_limit_redis_alias
                if await _redis_alive(alias):
                    logger.info(
                        "Throttle boot-fallback recovered (readyz probe); "
                        "rebuilding backend."
                    )
                    await reset_backend()
                    throttle = await get_throttle()
            healthy = await throttle.is_healthy()
            return HealthCheckResult(
                name="throttle",
                healthy=healthy,
                detail=throttle.backend_name,
            )
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                name="throttle",
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
            )

    _check.__name__ = "throttle_check"
    return _check


def breaker_check() -> Check:
    """Build a probe that calls ``is_healthy`` on the circuit-breaker registry.

    Mirrors :func:`throttle_check`. The breaker registry is a
    singleton (one registry serving every named breaker) so there is
    no alias to plumb — the registry's own ``backend_name`` is
    reported in ``detail`` for operator visibility. When the cached
    registry is the bare :class:`InMemoryRegistry` (boot-time
    fallback) the probe pings the configured
    ``circuit_breaker_redis_alias`` and, on success, calls
    :func:`circuit_breaker.provider.reset_backend` so the next
    ``get_registry`` call rebuilds against Redis.

    Returns:
        An async probe callable named ``breaker_check`` for logging.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the breaker registry, attempt boot-time recovery, then probe.

        Returns:
            ``HealthCheckResult`` named ``breaker`` with the outcome;
            the backend label is exposed via ``detail``.
        """
        try:
            from src.core.resilience.circuit_breaker.provider import (
                get_registry,
                reset_backend,
            )
            from src.core.runtime import get_settings

            registry = await get_registry()
            if registry.backend_name == "memory":
                alias = get_settings().circuit_breaker_redis_alias
                if await _redis_alive(alias):
                    logger.info(
                        "Breaker boot-fallback recovered (readyz probe); "
                        "rebuilding backend."
                    )
                    await reset_backend()
                    registry = await get_registry()
            healthy = await registry.is_healthy()
            return HealthCheckResult(
                name="breaker",
                healthy=healthy,
                detail=registry.backend_name,
            )
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                name="breaker",
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
            )

    _check.__name__ = "breaker_check"
    return _check
