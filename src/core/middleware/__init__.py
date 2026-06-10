"""Boilerplate-owned middleware re-exports.

Kit-owned middleware (request-id, body-limit, security-headers,
rate-limit-headers, exception-logging) is installed via
``resilience_kit.adapters.fastapi.install_middleware_stack``. The
modules here are the boilerplate-specific extras kept on top of that:

* :class:`MetricsMiddleware` — emits one ``http_request`` duration
  sample per request via :func:`src.core.metrics.record_duration`.
* :class:`RequestLoggingMiddleware` — structured request/response log
  with bounded body capture for the audit pipeline.
* :class:`SelectiveCORSMiddleware` — CORS with per-prefix exclusion;
  retained pending replacement by the kit's variant.
"""

from src.core.middleware.metrics_middleware import MetricsMiddleware
from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.core.middleware.selective_cors import SelectiveCORSMiddleware

__all__ = [
    "MetricsMiddleware",
    "RequestLoggingMiddleware",
    "SelectiveCORSMiddleware",
]
