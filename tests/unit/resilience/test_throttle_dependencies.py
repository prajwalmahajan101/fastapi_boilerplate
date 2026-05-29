"""Unit tests for the ``fixed_window_rate_limit`` FastAPI dependency.

Builds a tiny ``FastAPI`` app with two routes, exercises them through
``TestClient``, and asserts:

* the allowed request is gated by the throttle backend (which is the
  in-memory fallback in this test — no Redis required);
* the 4th request inside the window returns 429 via the central
  exception handler.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.core.exceptions import register_exception_handlers
from src.core.resilience.throttle import fixed_window_rate_limit


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get(
        "/global",
        dependencies=[Depends(fixed_window_rate_limit("global", "3/min"))],
    )
    async def hit() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_allows_up_to_limit_then_returns_429(client: TestClient) -> None:
    for _ in range(3):
        assert client.get("/global").status_code == 200
    blocked = client.get("/global")
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "RATE_LIMITED"
