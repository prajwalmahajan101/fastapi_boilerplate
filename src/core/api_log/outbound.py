"""``@log_outbound_request`` — fire-and-forget audit on outbound HTTP calls.

Decorate a service method that calls :class:`AsyncAPIClient`; each call
emits one ``api_logs`` row (success or failure) via the bounded
background queue. Reads request/response metadata from
:data:`outbound_response_meta_ctx` so the decorator never has to inspect
the wrapped function's signature.

Dormant: not currently applied to any service. Uncovered until a
service decorates an outbound call with ``@log_outbound_request``; do
not import from a request-path file without adding a matching test.
Tracked by ``tests/unit/scripts/test_no_dormant_imports.py``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from src.core.api_log.context import outbound_response_meta_ctx
from src.core.api_log.dispatch import CaptureState, capture_and_dispatch
from src.core.api_log.error_messages import build_error_message
from src.core.api_log.models import ApiLog, RequestDirection
from src.core.api_log.sanitizers import (
    UNSET,
    audit_safe,
    compute_ttl,
    redact_headers,
    serialize_body,
    truncate,
)
from src.core.context import get_request_id
from src.core.exceptions.utils import (
    exception_response_payload,
    exception_wire_status,
)
from src.core.runtime import get_settings

F = TypeVar("F", bound=Callable[..., Any])


def log_outbound_request(service_name: str) -> Callable[[F], F]:
    """Decorate a service method so each outbound HTTP call emits an audit row.

    Reads request/response metadata published by
    :meth:`AsyncAPIClient._request` into
    :data:`outbound_response_meta_ctx` so this decorator never has to
    inspect the wrapped function's arguments itself — call site can
    use any kwargs shape.

    Args:
        service_name: Logical service tag stored on every emitted log
            row (e.g. ``"payments_api"``).

    Returns:
        A decorator that wraps an async service method.
    """

    def decorator(func: F) -> F:
        """Wrap ``func`` with the outbound-log capture machinery.

        Args:
            func: The service method to wrap.

        Returns:
            The wrapped method with audit capture in its ``finally``.
        """

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Run ``func`` and emit an ``api_logs`` row for the outbound call.

            The wrapper forwards ``*args`` / ``**kwargs`` to ``func``
            verbatim and threads the call through
            :func:`capture_and_dispatch`. The per-call ``ApiLog`` row is
            materialised by :func:`_build_outbound_log`, which decides
            which kwargs land in the ``extra`` JSON column — see that
            function's ``_http_keys`` filter for the rules.

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments forwarded to ``func``;
                    :func:`_build_outbound_log` later filters HTTP
                    plumbing keys (method/url/headers/json/...) out
                    before folding the remainder into ``extra``.

            Returns:
                Whatever the wrapped method returned.

            Raises:
                Exception: Any exception ``func`` raises is re-raised
                    after the audit row is queued.
            """
            token = outbound_response_meta_ctx.set(None)

            def build_log(state: CaptureState) -> ApiLog:
                """Materialise the outbound ``ApiLog`` from captured state.

                Reads the per-call meta dict from
                :data:`outbound_response_meta_ctx` before the outer
                wrapper's ``finally`` resets the token, so the ContextVar
                still carries the request-scoped value.
                """
                exc_type = type(state.exc).__name__ if state.exc is not None else None
                exc_msg = (
                    build_error_message(state.exc) if state.exc is not None else None
                )
                return _build_outbound_log(
                    func_kwargs=kwargs,
                    service_name=service_name,
                    result=state.result,
                    duration_ms=state.elapsed_ms,
                    meta=outbound_response_meta_ctx.get(),
                    exc=state.exc,
                    exc_type=exc_type,
                    exc_msg=exc_msg,
                )

            try:
                return await capture_and_dispatch(
                    func,
                    args,
                    kwargs,
                    build_log,
                    service_name=service_name,
                    direction="outbound",
                )
            finally:
                outbound_response_meta_ctx.reset(token)

        return wrapper  # type: ignore[return-value]

    return decorator


def _build_outbound_log(
    func_kwargs: dict[str, Any],
    service_name: str,
    result: Any,
    duration_ms: float,
    meta: dict[str, Any] | None,
    exc: Exception | None,
    exc_type: str | None,
    exc_msg: str | None,
) -> ApiLog:
    """Materialise an outbound ``ApiLog`` from kwargs + HTTP-client meta.

    ``meta`` takes precedence over ``func_kwargs`` for HTTP fields
    (method/url/headers/body) so the audit reflects what actually went
    on the wire even if a decorator further inside rewrote them. Keys
    in ``func_kwargs`` that aren't HTTP plumbing land in the ``extra``
    JSON column for diagnostic context.

    When the call raised before the HTTP client published any meta
    (e.g. timeout, DNS failure, transport-layer ``APIError`` on a
    non-2xx response), the upstream status and body are recovered from
    the exception itself via :func:`exception_wire_status` and
    :func:`exception_response_payload` so the audit row still carries
    those columns.

    Args:
        func_kwargs: The decorated method's kwargs as received.
        service_name: Logical service tag for the row.
        result: Whatever the method returned.
        duration_ms: Wall time spent in the method.
        meta: Metadata published by ``AsyncAPIClient._request``.
        exc: The exception raised by the wrapped method (if any).
        exc_type: Class name of the raised exception, if any.
        exc_msg: Composite error message from :func:`build_error_message`.

    Returns:
        A populated ``ApiLog`` ready to be persisted.
    """
    settings = get_settings()

    method: str = (
        (meta.get("method") if meta else None) or func_kwargs.get("method") or ""
    )
    url: str = (meta.get("url") if meta else None) or func_kwargs.get("url") or ""

    raw_req_headers = (
        meta.get("request_headers") if meta else None
    ) or func_kwargs.get("headers")
    req_headers = redact_headers(raw_req_headers) if raw_req_headers else None

    query_params = (
        (meta.get("params") if meta else None) or func_kwargs.get("params")
    ) or None

    req_body: str | None = None
    if settings.api_log_capture_request_body:
        json_body = (
            meta.get("request_body_json") if meta else None
        ) or func_kwargs.get("json")
        data_body = (
            meta.get("request_body_data") if meta else None
        ) or func_kwargs.get("data")
        body_value = json_body if json_body is not None else data_body
        req_body = serialize_body(body_value, settings.api_log_max_body_size)

    status_code: int | None = None
    resp_headers: dict[str, str] | None = None
    if meta:
        status_code = meta.get("status_code")
        raw_resp_hdrs = meta.get("response_headers")
        if raw_resp_hdrs:
            resp_headers = redact_headers(raw_resp_hdrs)
    elif exc is not None:
        status_code = exception_wire_status(exc)

    resp_body: str | None = None
    if settings.api_log_capture_response_body:
        if result is not UNSET:
            resp_body = serialize_body(result, settings.api_log_max_body_size)
        elif meta and meta.get("response_body") is not None:
            resp_body = serialize_body(
                meta["response_body"], settings.api_log_max_body_size
            )
        elif exc is not None:
            payload = exception_response_payload(exc)
            if payload is not None:
                resp_body = serialize_body(payload, settings.api_log_max_body_size)

    _http_keys = {
        "method",
        "url",
        "auth_token",
        "auth_type",
        "headers",
        "params",
        "data",
        "json",
        "timeout",
        "check_ssrf",
    }
    extra_ctx = {
        k: audit_safe(v) for k, v in func_kwargs.items() if k not in _http_keys
    }
    extra: dict[str, Any] | None = extra_ctx or None

    return ApiLog(
        direction=RequestDirection.OUTBOUND,
        service_name=service_name,
        request_id=get_request_id(),
        environment=settings.app_environment,
        method=method.upper(),
        url=url,
        query_params=query_params,
        request_headers=req_headers,
        request_body=req_body,
        response_status_code=status_code,
        response_headers=resp_headers,
        response_body=resp_body,
        duration_ms=round(duration_ms, 3),
        error_type=exc_type,
        error_message=truncate(exc_msg, 2000) if exc_msg else None,
        ttl_expires_at=compute_ttl(),
        extra=extra,
    )


__all__ = ["log_outbound_request"]
