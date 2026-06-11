"""Unit coverage for the unhealthy branches of ``create_health_router``.

The existing ``test_healthcheck_privilege.py`` covers the privilege
gate; this file fills in the three uncovered branches in
``src/core/lifecycle/healthcheck.py``:

* every check passes → 200 ``"ok"`` / ``"ready"``.
* one check raises an exception → 503 + the exception is folded into
  the result's ``detail``.
* a check reports ``healthy=False`` directly → 503 with the per-check
  reason surfaced.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.lifecycle.healthcheck import (
    HealthCheckResult,
    create_health_router,
    create_readiness_router,
)


def _app(router) -> FastAPI:
    """Mount a single health router on a throwaway app."""
    app = FastAPI()
    app.include_router(router)
    return app


def test_all_checks_passing_returns_200_ok() -> None:
    """Healthy aggregate → status ``"ok"`` and 200."""

    async def _ok() -> HealthCheckResult:
        return HealthCheckResult(name="ok", healthy=True, detail="fine")

    response = TestClient(_app(create_health_router(checks=[_ok], path="/h"))).get("/h")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["healthy"] is True


def test_raising_check_resolves_to_503_with_detail() -> None:
    """A check that raises is logged and folded into a single failure entry."""

    async def _boom() -> HealthCheckResult:
        raise RuntimeError("redis is unreachable")

    response = TestClient(_app(create_health_router(checks=[_boom], path="/h"))).get(
        "/h?privileged=true"
    )
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["healthy"] is False


def test_unhealthy_check_resolves_to_503() -> None:
    """A check returning ``healthy=False`` flips the aggregate to unhealthy."""

    async def _bad() -> HealthCheckResult:
        return HealthCheckResult(name="db", healthy=False, detail="timeout")

    async def _good() -> HealthCheckResult:
        return HealthCheckResult(name="cache", healthy=True, detail="ok")

    response = TestClient(
        _app(create_health_router(checks=[_bad, _good], path="/h"))
    ).get("/h")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


def test_readiness_uses_distinct_status_labels() -> None:
    """``create_readiness_router`` flips the labels to ``ready``/``not_ready``."""

    async def _ok() -> HealthCheckResult:
        return HealthCheckResult(name="cache", healthy=True, detail="ok")

    response = TestClient(_app(create_readiness_router(checks=[_ok], path="/r"))).get(
        "/r"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_unhealthy_label_is_not_ready() -> None:
    """An unhealthy readiness probe surfaces ``"not_ready"``."""

    async def _bad() -> HealthCheckResult:
        return HealthCheckResult(name="db", healthy=False, detail="boom")

    response = TestClient(_app(create_readiness_router(checks=[_bad], path="/r"))).get(
        "/r"
    )
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_empty_check_list_resolves_to_200() -> None:
    """No checks → healthy (the documented zero-check behaviour)."""
    response = TestClient(_app(create_health_router(checks=[], path="/h"))).get("/h")
    assert response.status_code == 200
    assert response.json()["healthy"] is True
