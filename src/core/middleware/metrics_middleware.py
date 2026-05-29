"""``MetricsMiddleware`` — request duration → metrics shim.

Off by default. Set ``CoreSettings.metrics_middleware_enabled = True``
(or env ``METRICS_MIDDLEWARE_ENABLED=true``) to install it.

The middleware tees per-request duration into
:func:`src.core.metrics.record_duration` with ``event="http_request"``
and ``status="ok" | "error"`` based on the response status code. No
path / route label is included so cardinality stays bounded — route
identification lives in the structured log payload emitted by
``RequestLoggingMiddleware``.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.metrics import record_duration


class MetricsMiddleware(BaseHTTPMiddleware):
    """Measure end-to-end request duration and emit one metric per request."""

    async def dispatch(self, request: Request, call_next):
        """Run the next handler, then record ``http_request`` duration.

        Args:
            request: Incoming Starlette request (unused — only timing
                is captured here; richer context lives in
                ``RequestLoggingMiddleware``).
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The response from the next handler.
        """
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status_code = response.status_code if response is not None else 500
            record_duration(
                "http_request",
                duration_ms,
                status="ok" if status_code < 500 else "error",
            )


__all__ = ["MetricsMiddleware"]
