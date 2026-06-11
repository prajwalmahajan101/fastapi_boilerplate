"""Unit coverage for ``SelectiveCORSMiddleware``.

Exercises the two paths the middleware chooses between:

* a request whose path *does not* match any ``excluded_prefixes`` is
  delegated to the wrapped ``CORSMiddleware`` — preflight returns the
  standard ``Access-Control-Allow-*`` headers;
* a request whose path *does* match is short-circuited to the inner
  ASGI app — no CORS headers are added even for an explicit
  ``Origin`` request.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.middleware.selective_cors import SelectiveCORSMiddleware


@pytest.fixture
def app() -> FastAPI:
    """An app with two routes and the middleware skipping ``/internal``."""
    fastapi_app = FastAPI()

    @fastapi_app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @fastapi_app.get("/internal/webhook")
    async def webhook() -> dict[str, str]:
        return {"ok": "yes"}

    fastapi_app.add_middleware(
        SelectiveCORSMiddleware,
        excluded_prefixes=("/internal",),
        allow_origins=["https://example.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return fastapi_app


def test_non_excluded_path_passes_through_cors(app: FastAPI) -> None:
    """A non-excluded preflight gets standard CORS headers."""
    client = TestClient(app)
    response = client.options(
        "/api/ping",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"
    assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_non_excluded_path_origin_echoed_on_real_request(app: FastAPI) -> None:
    """A non-excluded GET reflects the allowed origin on the response."""
    response = TestClient(app).get(
        "/api/ping", headers={"Origin": "https://example.com"}
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"


def test_excluded_path_does_not_get_cors_headers(app: FastAPI) -> None:
    """A request to an excluded prefix bypasses CORS entirely."""
    response = TestClient(app).get(
        "/internal/webhook", headers={"Origin": "https://example.com"}
    )
    assert response.status_code == 200
    # No CORS layer was applied — the Allow-Origin header is absent.
    assert "access-control-allow-origin" not in response.headers


def test_disallowed_origin_does_not_echo(app: FastAPI) -> None:
    """A non-allowed origin gets no Access-Control-Allow-Origin echo."""
    response = TestClient(app).get(
        "/api/ping", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") != "https://evil.example"
