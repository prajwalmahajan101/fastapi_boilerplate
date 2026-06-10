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

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from resilience_kit.cache.provider import get_cache
from resilience_kit.circuit_breaker.provider import get_breaker
from resilience_kit.throttle.provider import get_throttle

from sqlalchemy import text

from src.core.context import get_request_id
from src.core.utils.db import get_app_engine
from src.core.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of one health/readiness probe."""

    name: str
    healthy: bool
    detail: str = ""


Check = Callable[[], Awaitable[HealthCheckResult]]


PrivilegeDep = Callable[..., Awaitable[bool]]


async def _default_privileged() -> bool:
    """Default privilege resolver — treats every caller as privileged.

    Preserves back-compat for callers that build routers without wiring
    an auth-aware predicate. ``src/api/health.py`` overrides this with
    a dependency that returns ``True`` only for superuser sessions, so
    unauthenticated probes get the masked body.

    Returns:
        ``True`` — full envelope is returned.
    """
    return True


def _envelope(
    *,
    status: str,
    healthy: bool,
    checks: list[HealthCheckResult],
    privileged: bool,
) -> dict[str, Any]:
    """Compose the JSON body returned by every health / readiness probe.

    When ``privileged`` is ``False`` the per-check ``checks`` list is
    omitted entirely so unauthenticated callers cannot enumerate the
    process's dependency topology. ``status`` / ``healthy`` /
    ``request_id`` are always returned.

    Args:
        status: Short human-readable label
            (``"healthy"`` / ``"ready"`` / their unhealthy counterparts).
        healthy: Whether every check passed.
        checks: Individual results — surfaced only when *privileged*.
        privileged: Whether the caller should see per-check detail.

    Returns:
        Dict with ``status``, ``healthy``, ``request_id``, and — when
        *privileged* — ``checks``.
    """
    body: dict[str, Any] = {
        "status": status,
        "healthy": healthy,
        "request_id": get_request_id(),
    }
    if privileged:
        body["checks"] = [
            {"name": c.name, "healthy": c.healthy, "detail": c.detail} for c in checks
        ]
    return body


def create_health_router(
    *,
    checks: list[Check] | None = None,
    healthy_status: str = "healthy",
    unhealthy_status: str = "unhealthy",
    path: str = "/health",
    privileged_dependency: PrivilegeDep | None = None,
) -> APIRouter:
    """Build a router exposing ``path`` — 200 when every check passes, 503 otherwise.

    Args:
        checks: Async probe callables; ``None`` is treated as ``[]``
            (always-healthy endpoint).
        healthy_status: ``status`` label when every check passes.
        unhealthy_status: ``status`` label when any check fails.
        path: URL path the probe is mounted at.
        privileged_dependency: Async FastAPI dependency returning
            ``True`` when the caller should see per-check detail. The
            default treats every caller as privileged so existing
            callers see no behavior change; ``src/api/health.py`` wires
            in a superuser-only predicate so anonymous probes get the
            masked body.

    Returns:
        A ``FastAPI.APIRouter`` ready to be included on the app.
    """
    router = APIRouter()
    check_callables = checks or []
    privilege_dep: PrivilegeDep = privileged_dependency or _default_privileged

    @router.get(path, include_in_schema=True)
    async def _health(privileged: bool = Depends(privilege_dep)) -> JSONResponse:
        """Run every configured check and return the aggregated probe response.

        Args:
            privileged: Resolved by ``privileged_dependency``; gates the
                per-check ``checks`` array in the response body.

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
            content=_envelope(
                status=status_text,
                healthy=healthy,
                checks=results,
                privileged=privileged,
            ),
        )

    return router


def create_readiness_router(
    *,
    checks: list[Check] | None = None,
    path: str = "/readiness",
    privileged_dependency: PrivilegeDep | None = None,
) -> APIRouter:
    """Build a readiness router — same shape as :func:`create_health_router`.

    Args:
        checks: Async probe callables.
        path: URL path the probe is mounted at.
        privileged_dependency: Async FastAPI dependency gating the
            per-check ``checks`` array. See :func:`create_health_router`.

    Returns:
        ``APIRouter`` with status labels ``"ready"`` / ``"not_ready"``.
    """
    return create_health_router(
        checks=checks,
        healthy_status="ready",
        unhealthy_status="not_ready",
        path=path,
        privileged_dependency=privileged_dependency,
    )


# ── Pre-built probes ───────────────────────────────────────────────────


async def db_check() -> HealthCheckResult:
    """Probe the application database with ``SELECT 1``.

    Returns:
        ``HealthCheckResult`` named ``"database"`` with the connection
        outcome (the error is folded into ``detail`` on failure).
    """
    try:
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


def cache_check(alias: str = "default") -> Check:
    """Build a probe that calls ``health_check`` on the named kit cache alias.

    Args:
        alias: Cache provider alias resolved by
            ``resilience_kit.cache.provider.get_cache``.

    Returns:
        An async probe callable; the closure carries ``alias`` so each
        cache alias can be checked independently.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the kit cache backend and probe its health.

        Returns:
            ``HealthCheckResult`` named ``cache[<alias>]`` with the
            outcome; the backend label is exposed via ``detail``.
        """
        try:
            cache = await get_cache(alias)
            snap = await cache.health_check()
            return HealthCheckResult(
                name=f"cache[{alias}]",
                healthy=snap.healthy,
                detail=snap.backend,
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
    """Build a probe that calls ``health_check`` on the kit throttle singleton.

    Returns:
        An async probe callable named ``throttle_check`` for logging.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the kit throttle backend and probe its health.

        Returns:
            ``HealthCheckResult`` named ``throttle`` with the outcome;
            the backend label is exposed via ``detail``.
        """
        try:
            throttle = await get_throttle()
            snap = await throttle.health_check()
            return HealthCheckResult(
                name="throttle",
                healthy=snap.healthy,
                detail=snap.backend,
            )
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                name="throttle",
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
            )

    _check.__name__ = "throttle_check"
    return _check


def breaker_check(name: str = "default") -> Check:
    """Build a probe that calls ``health_check`` on the kit circuit breaker.

    Args:
        name: Breaker name resolved by
            ``resilience_kit.circuit_breaker.provider.get_breaker``.

    Returns:
        An async probe callable named ``breaker_check`` for logging.
    """

    async def _check() -> HealthCheckResult:
        """Resolve the kit breaker and probe its health.

        Returns:
            ``HealthCheckResult`` named ``breaker`` with the outcome;
            the backend label is exposed via ``detail``.
        """
        try:
            breaker = await get_breaker(name)
            snap = await breaker.health_check()
            return HealthCheckResult(
                name="breaker",
                healthy=snap.healthy,
                detail=snap.backend,
            )
        except Exception as exc:  # noqa: BLE001
            return HealthCheckResult(
                name="breaker",
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
            )

    _check.__name__ = "breaker_check"
    return _check
