"""Unit coverage for ``RequestIdBridgeMiddleware``.

The bridge copies the kit's request-id ContextVar into the
boilerplate's so the response envelope, logging filter, and api-log
dispatch all see the same value. Tested in isolation against the kit's
RequestIdMiddleware ordering (kit inside → bridge outside, per
Starlette's prepend semantics).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from resilience_kit.middleware.request_id import RequestIdMiddleware

from src.core.context import get_request_id
from src.core.middleware.request_id_bridge import RequestIdBridgeMiddleware


def _app() -> FastAPI:
    """Build an app with the kit + bridge in production order."""
    app = FastAPI()

    @app.get("/probe")
    async def probe() -> dict[str, str | None]:
        return {"id": get_request_id()}

    # Starlette prepends — the LAST add_middleware runs FIRST (outermost).
    # Bridge must run *after* the kit's RequestId middleware so the
    # kit's ContextVar is already populated when ``bind_to`` runs.
    # Therefore: bridge first (inner), kit last (outer).
    app.add_middleware(RequestIdBridgeMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app


def test_inbound_request_id_propagates_to_envelope() -> None:
    """A supplied ``X-Request-ID`` reaches the boilerplate ContextVar."""
    app = _app()
    rid = "11111111-2222-3333-4444-555555555555"
    response = TestClient(app).get("/probe", headers={"X-Request-ID": rid})
    assert response.status_code == 200
    assert response.json()["id"] == rid
    assert response.headers.get("X-Request-ID") == rid


def test_missing_request_id_is_minted() -> None:
    """Without an inbound id the kit mints one; the bridge mirrors it."""
    app = _app()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    # Whatever the kit minted is reflected on both the body and the header.
    assert response.headers.get("X-Request-ID") == body["id"]
    # Sanity: a well-formed UUID-shape (kit's default minter).
    uuid.UUID(body["id"])  # raises if malformed


def test_non_http_scope_skips_bind() -> None:
    """Non-HTTP scopes (e.g. lifespan, websocket) pass through untouched."""
    # The middleware's lifespan/websocket short-circuit is exercised when
    # the FastAPI app starts up: TestClient enters the lifespan scope
    # before serving a request. We just need to construct + tear down
    # without exceptions and confirm the regular HTTP path still works.
    app = _app()
    with TestClient(app) as client:
        response = client.get("/probe")
        assert response.status_code == 200


@pytest.fixture(autouse=True)
def _reset_request_id():
    """Clear the boilerplate ContextVar after each test so leakage can't pass."""
    yield
    # The kit's bind_to context manager already resets on exit; this is
    # a belt-and-braces clear for the rare case a test runs without the
    # middleware chain.
    from src.core.context import request_id_ctx

    request_id_ctx.set(None)
