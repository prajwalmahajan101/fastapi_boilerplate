"""Unit test for ``MetricsMiddleware``.

We assemble a minimal FastAPI app with the middleware installed and
make a single request through ``TestClient``; the metrics shim is
already verified independently, so this test just confirms the
middleware wires status / duration into one log line per request.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.middleware import install_core_middleware


@pytest.fixture
def app_with_metrics() -> FastAPI:
    """Build a minimal FastAPI app with metrics middleware on."""
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict:
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("forced failure")

    install_core_middleware(
        app,
        cors_enabled=False,
        enable_security_headers=False,
        enable_body_size_limit=False,
        enable_rate_limit_headers=False,
        enable_metrics_middleware=True,
    )
    return app


def test_metrics_middleware_emits_one_sample_per_request(
    app_with_metrics: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    client = TestClient(app_with_metrics)

    response = client.get("/ok")
    assert response.status_code == 200

    samples = [r for r in caplog.records if getattr(r, "metric", None) == "duration"]
    assert len(samples) == 1
    assert samples[0].event == "http_request"  # type: ignore[attr-defined]
    assert samples[0].status == "ok"  # type: ignore[attr-defined]
    assert samples[0].duration_ms >= 0  # type: ignore[attr-defined]


def test_metrics_middleware_records_error_status(
    app_with_metrics: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    client = TestClient(app_with_metrics, raise_server_exceptions=False)

    response = client.get("/boom")
    assert response.status_code == 500

    samples = [r for r in caplog.records if getattr(r, "metric", None) == "duration"]
    assert len(samples) == 1
    assert samples[0].status == "error"  # type: ignore[attr-defined]


def test_metrics_middleware_off_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the middleware is not installed no duration metric is emitted."""
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict:
        return {"ok": True}

    install_core_middleware(
        app,
        cors_enabled=False,
        enable_security_headers=False,
        enable_body_size_limit=False,
        enable_rate_limit_headers=False,
    )
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    client = TestClient(app)
    client.get("/ok")

    samples = [r for r in caplog.records if getattr(r, "metric", None) == "duration"]
    assert samples == []
