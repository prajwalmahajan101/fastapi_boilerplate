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
from starlette.responses import Response

from src.core.api_log.dispatch import CaptureState, capture_and_dispatch
from src.core.api_log.error_messages import build_error_message
from src.core.api_log.models import ApiLog, RequestDirection
from src.core.api_log.sanitizers import (
    UNSET,
    compute_ttl,
    redact_headers,
    serialize_body,
    truncate,
)
from src.core.context import get_request_id
from src.core.runtime import get_settings

F = TypeVar("F", bound=Callable[..., Any])


def log_inbound_request(service_name: str) -> Callable[[F], F]:
    """Decorate a FastAPI route so each call emits an ``api_logs`` row.

    The wrapped handler must take ``request: Request`` as a kwarg so
    the decorator can read headers / body. Persistence is dispatched
    via :func:`capture_and_dispatch` — the audit write never blocks
    the response.

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
            # Read the request body *before* invoking the handler so the
            # fire-and-forget audit task does not race the ASGI receive
            # channel closing after the response has shipped. Starlette
            # caches the bytes on ``Request._body`` so the handler's own
            # ``await request.body()`` calls remain side-effect-free.
            req_body_raw: bytes | None = None
            if (
                request is not None
                and get_settings().api_log_capture_request_body
            ):
                req_body_raw = await request.body()

            def build_log(state: CaptureState) -> ApiLog:
                """Materialise the inbound ``ApiLog`` from captured state."""
                exc_type = (
                    type(state.exc).__name__ if state.exc is not None else None
                )
                exc_msg = (
                    build_error_message(state.exc)
                    if state.exc is not None
                    else None
                )
                return _build_inbound_log(
                    request=request,
                    req_body_raw=req_body_raw,
                    service_name=service_name,
                    result=state.result,
                    duration_ms=state.elapsed_ms,
                    exc_type=exc_type,
                    exc_msg=exc_msg,
                )

            return await capture_and_dispatch(func, args, kwargs, build_log)

        return wrapper  # type: ignore[return-value]

    return decorator


def _build_inbound_log(
    request: Request | None,
    req_body_raw: bytes | None,
    service_name: str,
    result: Any,
    duration_ms: float,
    exc_type: str | None,
    exc_msg: str | None,
) -> ApiLog:
    """Materialise an inbound ``ApiLog`` from request + handler outcome.

    Pure / synchronous: all I/O (request body read) happens in the
    wrapper before the response ships, so this helper never awaits.

    Args:
        request: The incoming ``Request`` (or ``None``).
        req_body_raw: Raw request bytes captured by the wrapper.
        service_name: Logical service tag for the row.
        result: Whatever the handler returned. When this is a Starlette
            ``Response`` subclass the rendered ``result.body`` and
            ``result.status_code`` are recorded; otherwise the raw
            object is serialized and status defaults to 200 / None.
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
        if req_body_raw is not None:
            req_body = truncate(
                req_body_raw.decode("utf-8", errors="replace"),
                settings.api_log_max_body_size,
            )

    resp_body: str | None = None
    if settings.api_log_capture_response_body and result is not UNSET:
        if isinstance(result, Response):
            resp_body = (
                truncate(
                    result.body.decode("utf-8", errors="replace"),
                    settings.api_log_max_body_size,
                )
                if result.body
                else None
            )
        else:
            resp_body = serialize_body(result, settings.api_log_max_body_size)

    if isinstance(result, Response):
        status_code: int | None = result.status_code
    else:
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
