"""``RequestLoggingMiddleware`` — start + finish log per request with duration."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.utils.log_sanitization import safe_log_dict
from src.core.utils.logging import get_logger
from src.core.utils.network import client_ip

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit structured access logs for every inbound HTTP request."""

    async def dispatch(self, request: Request, call_next):
        """Run the next handler and emit a structured access log entry.

        Args:
            request: Incoming Starlette request.
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The response from the next handler.
        """
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        caller_ip = client_ip(request)
        user_agent = request.headers.get("user-agent", "")

        logger.info(
            "request.start %s %s",
            method,
            path,
            extra=safe_log_dict(
                method=method,
                path=path,
                client_ip=caller_ip,
                user_agent=user_agent,
                query=str(request.url.query),
            ),
        )

        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status_code = response.status_code if response is not None else 500
            logger.info(
                "request.finish %s %s -> %d (%.2fms)",
                method,
                path,
                status_code,
                duration_ms,
                extra=safe_log_dict(
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                ),
            )
