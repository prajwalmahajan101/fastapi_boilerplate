"""``ExceptionLoggingMiddleware`` — last-resort structured log for any raised exception.

Re-raises after logging so the registered FastAPI exception handlers
still produce the response envelope.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.core.utils.log_sanitization import safe_log_dict, truncate_for_log
from src.core.utils.logging import get_logger

logger = get_logger(__name__)


class ExceptionLoggingMiddleware(BaseHTTPMiddleware):
    """Log any raised exception then re-raise so registered handlers respond."""

    async def dispatch(self, request: Request, call_next):
        """Run the next handler, structured-log any exception, then re-raise.

        Args:
            request: Incoming Starlette request.
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The response from the next handler.

        Raises:
            HTTPException: Re-raised after a structured log entry.
            Exception: Re-raised after a structured log entry.
        """
        try:
            return await call_next(request)
        except HTTPException as exc:
            logger.warning(
                "HTTPException in %s %s: %s",
                request.method,
                request.url.path,
                exc.detail,
                extra=safe_log_dict(
                    method=request.method,
                    path=request.url.path,
                    status_code=exc.status_code,
                    detail=truncate_for_log(str(exc.detail), 500),
                ),
            )
            raise
        except Exception as exc:
            logger.exception(
                "Unhandled exception in %s %s: %s",
                request.method,
                request.url.path,
                type(exc).__name__,
                extra=safe_log_dict(
                    method=request.method,
                    path=request.url.path,
                    exception_type=type(exc).__name__,
                    exception_message=truncate_for_log(str(exc), 500),
                ),
            )
            raise
