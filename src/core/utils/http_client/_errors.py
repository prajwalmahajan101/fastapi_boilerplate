"""Map aiohttp / asyncio failures to the project's typed exception families.

Extracted from ``AsyncAPIClient`` so the mapping is unit-testable in
isolation: a 5xx response, a transport hiccup, and a timeout each
need to land in a specific typed family so the resilience decorators
can decide whether to retry.

* ``ServerTimeoutError`` / ``asyncio.TimeoutError`` → ``ExternalTimeoutError``
* ``ClientResponseError`` (4xx) → ``APIError``
* Any other ``ClientError`` (DNS, SSL, connection reset) → ``TransientError``
* HTTP status ≥ 500 → ``TransientError`` (raised by the caller before
  ``raise_for_status`` fires)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from src.core.exceptions.api import APIError
from src.core.exceptions.infrastructure import ExternalTimeoutError, TransientError
from src.core.utils.logging import get_logger
from src.core.utils.ssrf import safe_host

logger = get_logger(__name__)


def raise_for_server_error(url: str, status: int) -> None:
    """Raise :class:`TransientError` when *status* is a 5xx response.

    Args:
        url: Target URL (used only to build the message — host-only).
        status: HTTP status code returned by the upstream.

    Raises:
        TransientError: When ``status >= 500``.
    """
    if status >= 500:
        raise TransientError(f"Server error from {safe_host(url)}: HTTP {status}")


@contextmanager
def map_aiohttp_errors(
    *,
    url: str,
    method: str,
    timeout: int,
    response_body_ref: dict[str, Any] | None = None,
    operation: str = "Request",
) -> Iterator[None]:
    """Translate aiohttp / asyncio failures into typed exceptions.

    The wrapped block runs the aiohttp call; any failure raised inside
    is logged with the request context and re-raised as the matching
    typed family. The block returns the response body normally on
    success — this helper does not touch it.

    Args:
        url: Absolute target URL (used for messages and logs).
        method: HTTP verb, for logs.
        timeout: Request timeout in seconds, for the timeout message.
        response_body_ref: Optional dict carrying the already-decoded
            response body under the ``body`` key, so the ``APIError``
            constructor can serialise it through ``_serialize_error_body``
            (the caller decides how to forward it).
        operation: Human label for the log line (``"Request"`` /
            ``"Download"``); appears in the error-line prefix.

    Yields:
        Nothing — wraps the caller's request block.

    Raises:
        ExternalTimeoutError: When the upstream timed out.
        APIError: When the upstream returned a 4xx response.
        TransientError: When aiohttp raised a transport-level error.
    """
    import asyncio

    from aiohttp import ClientError, ClientResponseError, ServerTimeoutError

    from src.core.utils.http_payloads import (
        serialize_error_body as _serialize_error_body,
    )

    try:
        yield
    except (ServerTimeoutError, asyncio.TimeoutError) as exc:
        logger.error(
            f"{operation} timeout",
            extra={"url": url, "method": method, "timeout": timeout},
        )
        raise ExternalTimeoutError(
            f"Request to {safe_host(url)} timed out after {timeout}s"
        ) from exc
    except ClientResponseError as exc:
        logger.error(
            f"{operation} client response error",
            extra={"status": exc.status, "url": url, "method": method},
        )
        body = (response_body_ref or {}).get("body")
        raise APIError(
            f"HTTP {exc.status} error: {exc.message}",
            status_code=exc.status,
            response_body=_serialize_error_body(body) if body is not None else None,
            details={"url": url, "method": method, "message": exc.message},
        ) from exc
    except ClientError as exc:
        logger.error(
            f"{operation} transport error",
            extra={"url": url, "method": method, "error_class": type(exc).__name__},
        )
        raise TransientError(f"Transport error contacting {safe_host(url)}") from exc


__all__ = ["map_aiohttp_errors", "raise_for_server_error"]
