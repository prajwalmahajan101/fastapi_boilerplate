"""Unit coverage for the four FastAPI exception handlers.

Drives each handler in isolation against a throwaway FastAPI app — no
service stack, no database — to exercise the envelope shapes the
handler is responsible for:

* ``custom_error_handler`` — every ``BaseCustomError`` subclass through
  the status-map registry; rate-limit subclasses surface their
  ``response_headers()``.
* ``kit_error_handler`` — ``ResilienceKitError`` bridges into the
  boilerplate envelope.
* ``request_validation_handler`` — FastAPI's 422 collapses into the
  per-field ``errors`` list.
* ``unhandled_exception_handler`` — generic 500 last-resort fallback.

The handler-registration order matters (``ResilienceKitError`` must
match before ``BaseCustomError``); this file proves the registry is
walked in the documented direction.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from resilience_kit.exceptions import RateLimitError as KitRateLimitError

from src.core.exceptions.auth import (
    APIKeyRevokedError,
    AuthenticationFailedError,
    PermissionDeniedError,
)
from src.core.exceptions.handlers import register_exception_handlers
from src.core.exceptions.rate_limit import RateLimitError
from src.core.exceptions.repository import EntityNotFoundError
from src.core.exceptions.validation import ValidationError


def _app() -> FastAPI:
    """Build a throwaway FastAPI app with all four handlers wired."""
    app = FastAPI()
    register_exception_handlers(app)
    return app


# ── custom_error_handler — status-map registry ─────────────────────


def test_validation_error_returns_400_envelope() -> None:
    """``ValidationError`` resolves to 400 + envelope code ``VALIDATION_ERROR``."""
    app = _app()

    @app.get("/boom")
    async def boom() -> None:
        raise ValidationError("bad input", details={"field": "name"})

    client = TestClient(app)
    response = client.get("/boom")
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["message"] == "bad input"
    assert body["errors"][0]["details"] == {"field": "name"}


def test_entity_not_found_returns_404() -> None:
    """``EntityNotFoundError`` resolves to 404 with the message preserved."""
    app = _app()

    @app.get("/missing")
    async def missing() -> None:
        raise EntityNotFoundError("Item", 42)

    client = TestClient(app)
    response = client.get("/missing")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False


def test_authentication_failed_returns_401() -> None:
    """``AuthenticationFailedError`` resolves to 401."""
    app = _app()

    @app.get("/forbid")
    async def forbid() -> None:
        raise AuthenticationFailedError("nope")

    response = TestClient(app).get("/forbid")
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_FAILED"


def test_api_key_revoked_preserves_subclass_code() -> None:
    """The registry walks subclasses first — revoked code, not parent code."""
    app = _app()

    @app.get("/revoked")
    async def revoked() -> None:
        raise APIKeyRevokedError()

    body = TestClient(app).get("/revoked").json()
    assert body["errors"][0]["code"] == "API_KEY_REVOKED"


def test_permission_denied_returns_403() -> None:
    """``PermissionDeniedError`` resolves to 403."""
    app = _app()

    @app.get("/perm")
    async def perm() -> None:
        raise PermissionDeniedError()

    assert TestClient(app).get("/perm").status_code == 403


def test_rate_limit_error_surfaces_response_headers() -> None:
    """The 429 handler emits the project ``RateLimitError``'s headers."""
    app = _app()

    @app.get("/throttle")
    async def throttle() -> None:
        raise RateLimitError(
            limit=10,
            window_seconds=60,
            retry_after=30,
            remaining=0,
            reset_at=1234567890,
        )

    response = TestClient(app).get("/throttle")
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "30"
    assert response.headers.get("X-RateLimit-Limit") == "10"
    assert response.headers.get("X-RateLimit-Remaining") == "0"


# ── kit_error_handler — ResilienceKitError bridge ───────────────────


def test_kit_rate_limit_bridges_into_envelope() -> None:
    """A kit ``RateLimitError`` is rewrapped into the boilerplate envelope."""
    app = _app()

    @app.get("/kit-rate")
    async def kit_rate() -> None:
        raise KitRateLimitError(
            limit=5,
            remaining=0,
            reset_at=1234567890,
            retry_after=12.0,
            scope="auth",
        )

    response = TestClient(app).get("/kit-rate")
    assert response.status_code == 429
    body = response.json()
    assert body["success"] is False
    assert body["errors"]
    # The kit's error_code surfaces in the envelope.
    assert "RATE" in body["errors"][0]["code"].upper()


# ── request_validation_handler — 422 contract ───────────────────────


def test_request_validation_returns_per_field_envelope() -> None:
    """FastAPI validation errors collapse into the 422 envelope."""
    app = _app()

    class Body(BaseModel):
        name: str
        age: int

    @app.post("/payload")
    async def take(body: Body) -> dict[str, str]:
        del body
        return {"ok": "yes"}

    response = TestClient(app).post("/payload", json={"name": "Ada"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Validation failed."
    assert body["errors"], body
    assert all(err["code"] == "VALIDATION_ERROR" for err in body["errors"])


# ── unhandled_exception_handler — last-resort 500 ──────────────────


def test_unhandled_exception_returns_generic_500() -> None:
    """Bare ``Exception`` falls through to the catch-all handler."""
    app = _app()

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/explode")
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "INTERNAL_SERVER_ERROR"


# ── handler ordering — kit before base, custom before generic ──────


def test_kit_error_handler_takes_priority_over_custom() -> None:
    """``ResilienceKitError`` matches the kit handler, not the custom one."""
    app = _app()

    @app.get("/dual")
    async def dual() -> None:
        # A kit error should hit kit_error_handler; if it leaked to
        # custom_error_handler the isinstance assertion would raise 500.
        raise KitRateLimitError(
            limit=1, remaining=0, reset_at=0, retry_after=1.0, scope="auth"
        )

    assert TestClient(app).get("/dual").status_code == 429
