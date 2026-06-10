"""End-to-end smoke tests for the example wiring.

Drive the real FastAPI app through ``TestClient`` (no lifespan — see
``conftest.py``) to prove the request path is sound: middleware stack,
rate-limit dependency (in-memory fallback), inbound audit decorator (no-op
backend), envelope serialisation, and request-id propagation all cooperate
on a live request. Delete or adapt these once the ``/hello`` example route
is gone.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_hello_default(client: TestClient) -> None:
    """``GET /api/v1/hello`` returns the standard success envelope."""
    response = client.get("/api/v1/hello")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Greeting generated."
    assert body["data"] == {"message": "Hello, world!"}
    assert body["errors"] is None
    # request-id middleware always stamps a value onto the envelope.
    assert body["request_id"]


def test_hello_named(client: TestClient) -> None:
    """The ``name`` query parameter is reflected in the greeting."""
    response = client.get("/api/v1/hello", params={"name": "Ada"})
    assert response.status_code == 200
    assert response.json()["data"] == {"message": "Hello, Ada!"}


def test_request_id_is_propagated(client: TestClient) -> None:
    """The echoed ``X-Request-ID`` header matches the envelope's request_id."""
    response = client.get("/api/v1/hello")
    header_rid = response.headers.get("X-Request-ID")
    assert header_rid
    assert response.json()["request_id"] == header_rid


def test_rate_limit_headers_present(client: TestClient) -> None:
    """Throttle 429 carries ``Retry-After`` + ``X-RateLimit-*`` headers.

    The kit (post-M7) only emits rate-limit headers on the rejection
    response, not on every allowed request — different from the
    pre-kit boilerplate behavior. Drive the endpoint past its
    ``60/min`` budget and assert the 429 carries the canonical
    header set defined by
    ``resilience_kit.exceptions.RateLimitError.response_headers``.
    """
    last_response = None
    for _ in range(70):
        last_response = client.get("/api/v1/hello")
        if last_response.status_code == 429:
            break
    assert last_response is not None and last_response.status_code == 429
    assert last_response.headers.get("Retry-After")
    assert last_response.headers.get("X-RateLimit-Limit") == "60"
    assert "X-RateLimit-Remaining" in last_response.headers
