"""``@log_inbound_request`` / ``@log_outbound_request`` — fire-and-forget audit capture.

Inbound: decorate a FastAPI route handler that takes ``request: Request``::

    @router.post("/webhook")
    @log_inbound_request(service_name="webhook")
    async def webhook(request: Request, payload: WebhookSchema):
        ...

Outbound: decorate any service method that calls ``AsyncAPIClient``. The
client publishes the full HTTP metadata to ``outbound_response_meta_ctx``
right before returning, so the decorator does not need to inspect the
request manually::

    @log_outbound_request(service_name="payments_api")
    @retry_with_exponential_backoff(max_retries=2, exceptions=(APIError,))
    async def charge(amount): ...

Persistence is dispatched via ``asyncio.create_task`` so the calling
coroutine never blocks on disk I/O. ``drain_pending_logs`` waits for the
in-flight tasks during lifespan shutdown.
"""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from fastapi import Request

from src.core.api_log.context import outbound_response_meta_ctx
from src.core.api_log.models import ApiLog, RequestDirection
from src.core.context import get_request_id
from src.core.runtime import get_settings
from src.core.utils.fire_and_forget import FireAndForgetQueue, register
from src.core.utils.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_UNSET = object()

# Generous cap because this path receives every
# inbound *and* every outbound HTTP call. The queue drops new
# submissions with a warning once it hits this many in-flight tasks.
# Registered so the lifespan's ``drain_all`` reaches it without an
# extra import.
_queue = register(FireAndForgetQueue(max_pending=2000, name="api_log"))


# ── Helpers ──────────────────────────────────────────────────────────────


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values replaced by ``[REDACTED]``.

    Header names are matched case-insensitively against
    ``api_log_sensitive_headers`` (Authorization, X-API-Key, Cookie, …).

    Args:
        headers: Raw request or response headers.

    Returns:
        Sanitised copy safe to persist in the audit log.
    """
    sensitive = {h.lower() for h in get_settings().api_log_sensitive_headers}
    return {
        k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in headers.items()
    }


def _truncate(text: str | None, max_len: int) -> str | None:
    """Cap ``text`` at ``max_len`` chars, appending an ellipsis marker.

    Keeps the audit log column from blowing past its width limit while
    leaving an obvious "this was truncated" marker for diagnostics.

    Args:
        text: Source string (may be ``None``).
        max_len: Maximum retained length before truncation.

    Returns:
        ``None`` when input is ``None``; otherwise the original string
        or a truncated copy ending in ``"…[truncated]"``.
    """
    if text is None:
        return None
    return text if len(text) <= max_len else text[:max_len] + "…[truncated]"


def _serialize_body(value: Any, max_len: int) -> str | None:
    """Render ``value`` as a string body of at most ``max_len`` chars.

    Strings pass through; bytes are UTF-8-decoded with errors replaced;
    everything else is JSON-dumped with ``default=str`` so the call
    never raises on unexpected payload shapes.

    Args:
        value: Body payload (str, bytes, dict, list, model, etc.).
        max_len: Maximum length passed to :func:`_truncate`.

    Returns:
        Truncated string, ``None`` when input is ``None`` / ``_UNSET``,
        or ``None`` on a serialization failure (logged-then-swallowed).
    """
    if value is None or value is _UNSET:
        return None
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = json.dumps(value, default=str)
        return _truncate(text, max_len)
    except Exception:  # noqa: BLE001
        return None


def _compute_ttl() -> int | None:
    """Return a unix-epoch expiry derived from ``api_log_ttl_days``.

    ``ttl_expires_at`` is consumed by a downstream pruning job; ``None``
    means "no expiry / keep forever".

    Returns:
        Unix timestamp ``ttl_days`` from now, or ``None`` when the
        setting is ``0`` / unset.
    """
    days = get_settings().api_log_ttl_days
    if not days:
        return None
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


def _build_error_message(exc: Exception) -> str:
    """Compose a single-line error string from ``exc`` for the audit row.

    For ``APIError`` subclasses, also folds in ``status_code``,
    ``response_body``, and ``details`` so the audit row carries the full
    upstream context.

    Args:
        exc: The exception raised by the wrapped handler.

    Returns:
        Pipe-delimited summary string, e.g.
        ``"upstream rejected … | status_code=502 | response_body=..."``.
    """
    from src.core.exceptions.api import APIError

    parts: list[str] = [str(exc)]
    if isinstance(exc, APIError):
        if exc.status_code is not None:
            parts.append(f"status_code={exc.status_code}")
        if exc.response_body:
            parts.append(f"response_body={exc.response_body}")
        if exc.details:
            parts.append(f"details={exc.details}")
    return " | ".join(parts)


def _fire_and_forget(coro: Any) -> None:
    """Submit ``coro`` to the bounded background queue.

    Submissions are dropped with a warning when the queue is at
    capacity — see :class:`FireAndForgetQueue`. The audit log must
    never block the inbound/outbound hot path.

    Args:
        coro: The persistence coroutine to schedule.
    """
    _queue.submit(coro)


async def _persist_log(log: ApiLog) -> None:
    """Save ``log`` — never raises (fire-and-forget contract).

    The repository's ``save`` may raise on DB outages; logging the
    failure keeps the producer running and lets operators see the
    backend health without affecting the request path.

    Args:
        log: Populated ``ApiLog`` record to persist.
    """
    try:
        from src.core.api_log.factory import get_repository

        await get_repository().save(log)
    except Exception:  # noqa: BLE001
        logger.exception("API log save failed", extra={"log_id": log.log_id})


# ── Inbound decorator ────────────────────────────────────────────────────


def log_inbound_request(service_name: str) -> Callable[[F], F]:
    """Decorate a FastAPI route so each call emits an ``api_logs`` row.

    The wrapped handler must take ``request: Request`` as a kwarg so
    the decorator can read headers / body. Persistence is dispatched
    via :func:`_fire_and_forget` — the audit write never blocks the
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
            start = time.perf_counter()
            result: Any = _UNSET
            exc_type: str | None = None
            exc_msg: str | None = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                exc_type = type(exc).__name__
                exc_msg = _build_error_message(exc)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                _fire_and_forget(
                    _build_and_persist_inbound_log(
                        request=request,
                        service_name=service_name,
                        result=result,
                        duration_ms=duration_ms,
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
        exc_msg: Composite error message from :func:`_build_error_message`.
    """
    log = await _build_inbound_log(
        request=request,
        service_name=service_name,
        result=result,
        duration_ms=duration_ms,
        exc_type=exc_type,
        exc_msg=exc_msg,
    )
    await _persist_log(log)


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
        exc_msg: Composite error message from :func:`_build_error_message`.

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
        req_headers = _redact_headers(dict(request.headers))
        if settings.api_log_capture_request_body:
            raw = await request.body()
            req_body = _truncate(
                raw.decode("utf-8", errors="replace") if raw else None,
                settings.api_log_max_body_size,
            )

    resp_body: str | None = None
    if settings.api_log_capture_response_body and result is not _UNSET:
        resp_body = _serialize_body(result, settings.api_log_max_body_size)

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
        error_message=_truncate(exc_msg, 2000) if exc_msg else None,
        ttl_expires_at=_compute_ttl(),
    )


# ── Outbound decorator ───────────────────────────────────────────────────


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

            Args:
                *args: Positional arguments forwarded to ``func``.
                **kwargs: Keyword arguments — captured into the
                    ``extra`` JSON column for diagnostic context.

            Returns:
                Whatever the wrapped method returned.

            Raises:
                Exception: Any exception ``func`` raises is re-raised
                    after the audit row is queued.
            """
            token = outbound_response_meta_ctx.set(None)
            start = time.perf_counter()
            result: Any = _UNSET
            exc_type: str | None = None
            exc_msg: str | None = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                exc_type = type(exc).__name__
                exc_msg = _build_error_message(exc)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                meta = outbound_response_meta_ctx.get()
                outbound_response_meta_ctx.reset(token)
                _fire_and_forget(
                    _persist_log(
                        _build_outbound_log(
                            func_kwargs=kwargs,
                            service_name=service_name,
                            result=result,
                            duration_ms=duration_ms,
                            meta=meta,
                            exc_type=exc_type,
                            exc_msg=exc_msg,
                        )
                    )
                )

        return wrapper  # type: ignore[return-value]

    return decorator


def _build_outbound_log(
    func_kwargs: dict[str, Any],
    service_name: str,
    result: Any,
    duration_ms: float,
    meta: dict[str, Any] | None,
    exc_type: str | None,
    exc_msg: str | None,
) -> ApiLog:
    """Materialise an outbound ``ApiLog`` from kwargs + HTTP-client meta.

    ``meta`` takes precedence over ``func_kwargs`` for HTTP fields
    (method/url/headers/body) so the audit reflects what actually went
    on the wire even if a decorator further inside rewrote them. Keys
    in ``func_kwargs`` that aren't HTTP plumbing land in the ``extra``
    JSON column for diagnostic context.

    Args:
        func_kwargs: The decorated method's kwargs as received.
        service_name: Logical service tag for the row.
        result: Whatever the method returned.
        duration_ms: Wall time spent in the method.
        meta: Metadata published by ``AsyncAPIClient._request``.
        exc_type: Class name of the raised exception, if any.
        exc_msg: Composite error message from :func:`_build_error_message`.

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
    req_headers = _redact_headers(raw_req_headers) if raw_req_headers else None

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
        req_body = _serialize_body(body_value, settings.api_log_max_body_size)

    status_code: int | None = None
    resp_headers: dict[str, str] | None = None
    if meta:
        status_code = meta.get("status_code")
        raw_resp_hdrs = meta.get("response_headers")
        if raw_resp_hdrs:
            resp_headers = _redact_headers(raw_resp_hdrs)

    resp_body: str | None = None
    if settings.api_log_capture_response_body:
        if result is not _UNSET:
            resp_body = _serialize_body(result, settings.api_log_max_body_size)
        elif meta and meta.get("response_body") is not None:
            resp_body = _serialize_body(
                meta["response_body"], settings.api_log_max_body_size
            )

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
    extra_ctx = {k: v for k, v in func_kwargs.items() if k not in _http_keys}
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
        error_message=_truncate(exc_msg, 2000) if exc_msg else None,
        ttl_expires_at=_compute_ttl(),
        extra=extra,
    )
