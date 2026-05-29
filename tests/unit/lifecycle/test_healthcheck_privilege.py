"""Unit tests for the privileged-only ``checks`` array in health envelopes.

Anonymous and non-superuser callers must see ``{status, healthy,
request_id}`` only; superuser callers additionally see the per-check
``checks`` list. The privileged predicate is wired in as a FastAPI
dependency so the test simply overrides it.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.lifecycle.healthcheck import (
    HealthCheckResult,
    create_health_router,
)


async def _ok() -> HealthCheckResult:
    """Return one always-healthy check named ``database`` for assertions."""
    return HealthCheckResult(name="database", healthy=True, detail="connected")


def _build_app(privileged: bool) -> TestClient:
    """Mount a single-probe router whose privilege dep returns *privileged*."""

    async def _dep() -> bool:
        return privileged

    app = FastAPI()
    app.include_router(
        create_health_router(
            checks=[_ok],
            path="/p",
            privileged_dependency=_dep,
        )
    )
    return TestClient(app)


def test_unprivileged_response_omits_checks_array():
    """Anonymous / non-superuser callers must not see the per-check list."""
    client = _build_app(privileged=False)
    body = client.get("/p").json()
    assert body["status"] == "healthy"
    assert body["healthy"] is True
    assert "request_id" in body
    assert "checks" not in body


def test_privileged_response_includes_checks_array():
    """Superuser callers see the per-check list with backend labels."""
    client = _build_app(privileged=True)
    body = client.get("/p").json()
    assert body["checks"] == [
        {"name": "database", "healthy": True, "detail": "connected"},
    ]


def test_default_dependency_keeps_back_compat():
    """Callers that pass no privilege dep see the full body (back-compat)."""
    app = FastAPI()
    app.include_router(create_health_router(checks=[_ok], path="/p"))
    body = TestClient(app).get("/p").json()
    assert "checks" in body and body["checks"][0]["name"] == "database"
