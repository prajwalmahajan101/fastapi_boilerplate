"""Health / readiness routers — thin wrappers around the core builders.

Two builders so the same probes can be mounted at two locations:

* ``build_root_health_router()`` — ``/healthz`` + ``/readyz`` at the URL
  root, the conventional path for kubernetes / load-balancer probes.
* ``build_api_health_router()`` — ``/health`` + ``/readiness`` inside the
  ``/api`` namespace, for client tooling that scopes everything under
  ``/api``.

Both call the same probe (``db_check`` over the shared application engine).
"""

from __future__ import annotations

from fastapi import APIRouter

from src.core.lifecycle.healthcheck import (
    breaker_check,
    cache_check,
    create_health_router,
    create_readiness_router,
    db_check,
    throttle_check,
)


def build_root_health_router() -> APIRouter:
    """Return ``/healthz`` + ``/readyz`` for kubernetes-style probes.

    ``/healthz`` is a liveness probe (DB only). ``/readyz`` is a
    readiness probe and pings the resilience cache + throttle +
    circuit-breaker backends so traffic is only routed once each
    backend (Redis or its in-memory fallback) is responding. All
    three resilience checks also double as the recovery trigger: the
    backend's ``BackendHealth`` flips back to ``ACTIVE`` on a
    successful probe, and a *boot-time* bare in-memory fallback
    gets torn down (next provider call rebuilds against Redis) when
    the direct ``PING`` succeeds — see :func:`cache_check`,
    :func:`throttle_check`, :func:`breaker_check`.

    Returns:
        ``APIRouter`` combining the two root-level probe routes.
    """
    health = create_health_router(checks=[db_check], path="/healthz")
    ready = create_readiness_router(
        checks=[
            db_check,
            cache_check("default"),
            throttle_check(),
            breaker_check(),
        ],
        path="/readyz",
    )
    combined = APIRouter()
    combined.include_router(health)
    combined.include_router(ready)
    return combined


def build_api_health_router() -> APIRouter:
    """Return ``/health`` + ``/readiness`` for the ``/api`` namespace.

    Same split as ``build_root_health_router`` — ``/health`` is DB-only
    (liveness), ``/readiness`` adds the resilience cache + throttle +
    circuit-breaker checks (each also acts as a recovery trigger when
    Redis becomes reachable after a boot-time fallback).

    Returns:
        ``APIRouter`` combining the two API-scoped probe routes.
    """
    health = create_health_router(checks=[db_check], path="/health")
    ready = create_readiness_router(
        checks=[
            db_check,
            cache_check("default"),
            throttle_check(),
            breaker_check(),
        ],
        path="/readiness",
    )
    combined = APIRouter()
    combined.include_router(health)
    combined.include_router(ready)
    return combined


__all__ = ["build_api_health_router", "build_root_health_router"]
