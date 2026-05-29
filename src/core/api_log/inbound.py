"""``@log_inbound_request`` — fire-and-forget audit on FastAPI routes.

Decorate a route that takes ``request: Request`` as a kwarg; each call
emits one ``api_logs`` row (success or failure) via the bounded
background queue. The audit write never blocks the response.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import Request

from src.core.api_log.dispatch import fire_and_forget, persist_log
from src.core.api_log.error_messages import build_error_message
from src.core.api_log.models import ApiLog, RequestDirection
from src.core.api_log.sanitizers import (
    _UNSET,
    compute_ttl,
    redact_headers,
    serialize_body,
    truncate,
)
from src.core.context import get_request_id
from src.core.runtime import get_settings
from src.core.utils.timing import perf_timer

F = TypeVar("F", bound=Callable[..., Any])


def log_inbound_request(service_name: str) -> Callable[[F], F]:
    """Decorate a FastAPI route so each call emits an ``api_logs`` row.

    The wrapped handler must take ``request: Request`` as a kwarg so
    the decorator can read headers / body. Persistence is dispatched
    via :func:`fire_and_forget` — the audit write never blocks the
    response.

    Args:
        service_name: Logical service tag stored on every emitted log
            row (e.g. ``"example_api"``, ``"webhook"``).

    Returns:
        A decorator that wraps an async route handler.
    """

    def decorator(func: F) -> F:
        """Wrap ``func`` with the inbound-log capture machinery.

        Args:
            func: The route handler to wrap.

        Returns:
            The wrapped handler with audit capture in its ``finally``.
        """

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Run ``func`` and emit an ``api_logs`` row on success or failure.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments (``request`` is read here).

            Returns:
                Whatever the wrapped handler returned.

            Raises:
                Exception: Any exception ``func`` raises is re-raised
                    after the audit row is queued.
            """
            request: Request | None = kwargs.get("request")
            result: Any = _UNSET
            exc_type: str | None = None
            exc_msg: str | None = None
            with perf_timer() as t:
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as exc:
                    exc_type = type(exc).__name__
                    exc_msg = build_error_message(exc)
                    raise
                finally:
                    fire_and_forget(
                        _build_and_persist_inbound_log(
                            request=request,
                            service_name=service_name,
                            result=result,
                            duration_ms=float(t.elapsed_ms),
                            exc_type=exc_type,
                            exc_msg=exc_msg,
                        )
                    )

        return wrapper  # type: ignore[return-value]

    return decorator


async def _build_and_persist_inbound_log(
    request: Request | None,
    service_name: str,
    result: Any,
    duration_ms: float,
    exc_type: str | None,
    exc_msg: str | None,
) -> None:
    """Compose and persist an inbound ``ApiLog`` row.

    Pulled out of :func:`log_inbound_request`'s ``finally`` so the
    body is testable in isolation.

    Args:
        request: The incoming ``Request`` (or ``None`` when the route
            did not declare one as a kwarg).
        service_name: Logical service tag for the row.
        result: Whatever the handler returned (or ``_UNSET`` on
            failure).
        duration_ms: Wall time spent inside the handler.
        exc_type: Class name of the raised exception, if any.
        exc_msg: Composite error message from :func:`build_error_message`.
    """
    log = await _build_inbound_log(
        request=request,
        service_name=service_name,
        result=result,
        duration_ms=duration_ms,
        exc_type=exc_type,
        exc_msg=exc_msg,
    )
    await persist_log(log)


async def _build_inbound_log(
    request: Request | None,
    service_name: str,
    result: Any,
    duration_ms: float,
    exc_type: str | None,
    exc_msg: str | None,
) -> ApiLog:
    """Materialise an inbound ``ApiLog`` from request + handler outcome.

    Reads the request body when ``api_log_capture_request_body`` is
    on (which calls ``await request.body()`` — that consumes the
    stream but FastAPI has already cached the body by this point, so
    handlers' own ``request.body()`` calls are unaffected).

    Args:
        request: The incoming ``Request`` (or ``None``).
        service_name: Logical service tag for the row.
        result: Whatever the handler returned.
        duration_ms: Wall time spent in the handler.
        exc_type: Class name of the raised exception, if any.
        exc_msg: Composite error message from :func:`build_error_message`.

    Returns:
        A populated ``ApiLog`` ready to be persisted.
    """
    settings = get_settings()
    method = url = ""
    query_params: dict[str, Any] | None = None
    req_headers: dict[str, str] | None = None
    req_body: str | None = None

    if request is not None:
        method = request.method
        url = str(request.url)
        query_params = dict(request.query_params) or None
        req_headers = redact_headers(dict(request.headers))
        if settings.api_log_capture_request_body:
            raw = await request.body()
            req_body = truncate(
                raw.decode("utf-8", errors="replace") if raw else None,
                settings.api_log_max_body_size,
            )

    resp_body: str | None = None
    if settings.api_log_capture_response_body and result is not _UNSET:
        resp_body = serialize_body(result, settings.api_log_max_body_size)

    status_code = 200 if exc_type is None else None

    return ApiLog(
        direction=RequestDirection.INBOUND,
        service_name=service_name,
        request_id=get_request_id(),
        environment=settings.app_environment,
        method=method,
        url=url,
        query_params=query_params,
        request_headers=req_headers,
        request_body=req_body,
        response_status_code=status_code,
        response_body=resp_body,
        duration_ms=round(duration_ms, 3),
        error_type=exc_type,
        error_message=truncate(exc_msg, 2000) if exc_msg else None,
        ttl_expires_at=compute_ttl(),
    )


__all__ = ["log_inbound_request"]
