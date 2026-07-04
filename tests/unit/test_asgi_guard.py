"""Unit tests for :class:`BearerTokenGuard` (ISSUE-040).

The Prometheus ``/metrics`` mount is a raw ASGI app, so it is protected by
``BearerTokenGuard`` rather than a FastAPI dependency. These tests drive the
guard through a real mount: no bearer → 401, wrong token → 401, correct
token → the inner app runs.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from src.core.asgi_guard import BearerTokenGuard

_TOKEN = "s3cr3t-scrape-token"  # noqa: S105 — test fixture, not a real secret


async def _inner(scope: dict, receive, send) -> None:
    """Trivial ASGI app standing in for the Prometheus exposition app."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"metrics-body"})


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.mount("/metrics", BearerTokenGuard(_inner, token=_TOKEN))
    return TestClient(app)


def test_missing_authorization_is_rejected(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_wrong_token_is_rejected(client: TestClient) -> None:
    resp = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_correct_token_reaches_inner_app(client: TestClient) -> None:
    resp = client.get("/metrics", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200
    assert resp.text == "metrics-body"


def test_empty_token_construction_fails() -> None:
    with pytest.raises(ValueError, match="non-empty token"):
        BearerTokenGuard(_inner, token="")
