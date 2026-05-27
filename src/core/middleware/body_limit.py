"""``ContentLengthLimitMiddleware`` — reject inbound bodies past a configured cap.

Wired before ``RequestLoggingMiddleware`` so a rejected oversize body
short-circuits the request entirely — the audit decorator never reads
the (potentially huge) body into memory just to write it to
``api_logs``.

The middleware honours :attr:`CoreSettings.max_request_body_bytes` and
returns the standard error envelope on rejection so callers see a
consistent 413 shape instead of Starlette's default plain-text default.

Two enforcement paths:

1. **``Content-Length`` declared and too large** → reject immediately
   with 413 before reading the body. This is the common case for
   well-behaved clients.
2. **``Content-Length`` absent (chunked or unknown length)** → wrap
   the ASGI ``receive`` callable to count bytes as they stream in;
   abort with 413 the moment the total crosses the cap.

Both paths return the project's standard ``ErrorResponse`` envelope so
clients see the same shape they would for any other error.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.responses.envelope import ErrorResponse
from src.core.responses.schemas import ErrorDetail
from src.core.runtime import get_settings


class ContentLengthLimitMiddleware:
    """ASGI middleware that 413s any request whose body exceeds a byte cap."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app`` for body-size enforcement.

        Args:
            app: The downstream ASGI application.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject oversized HTTP requests; pass everything else through.

        Args:
            scope: ASGI scope dict.
            receive: ASGI receive callable.
            send: ASGI send callable.

        Raises:
            Exception: Re-raised from the wrapped app on the pre-rejection
                path. Exceptions raised after a 413 has been written
                (typically Starlette's ``ClientDisconnect`` from the
                half-consumed body) are swallowed, since they are part of
                the rejection unwind rather than a real failure.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = get_settings().max_request_body_bytes
        if max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        # Fast path: trust a declared Content-Length when present.
        content_length = _content_length_from_headers(scope)
        if content_length is not None and content_length > max_bytes:
            await _send_413(send, max_bytes)
            return

        # Streaming path: cap the cumulative body size as it arrives.
        # Even when Content-Length is set, we still wrap receive to defend
        # against clients that lie about the length.
        state: dict[str, object] = {"received": 0, "rejected": False}

        async def limited_receive() -> Message:
            """Receive the next ASGI message; abort if the body cap is hit.

            Returns:
                The next ASGI ``Message``, unmodified, unless the body
                cap has been exceeded — in which case an in-band 413
                is sent and an empty disconnect frame is yielded so
                the downstream app stops reading.
            """
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > max_bytes:
                    if not state["rejected"]:
                        state["rejected"] = True
                        await _send_413(send, max_bytes)
                    # After the 413 is emitted, the downstream app must
                    # not see any further body bytes — signal disconnect.
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Message) -> None:
            """Drop downstream sends once the 413 has been written.

            Starlette raises ``ClientDisconnect`` when it sees the
            ``http.disconnect`` returned above; the unhandled-exception
            handler then tries to emit a 500. The ASGI server would
            reject that ("Response already started") but the audit log
            still gets a spurious 5xx row. Swallowing post-413 writes
            here keeps the audit log honest — the request was a clean
            413, not a 500.

            Args:
                message: Outgoing ASGI message from the wrapped app.
            """
            if state["rejected"]:
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:  # noqa: BLE001
            # Once the 413 has shipped the wrapped app may raise on the
            # half-consumed body (Starlette's ``ClientDisconnect``, etc.).
            # The exception is part of the rejection path, not a real
            # failure — re-raise only when we did not reject the body.
            if not state["rejected"]:
                raise


def _content_length_from_headers(scope: Scope) -> int | None:
    """Extract a positive integer ``Content-Length`` from ASGI headers.

    Args:
        scope: ASGI scope dict.

    Returns:
        The parsed length, or ``None`` when the header is missing,
        malformed, or non-positive.
    """
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                parsed = int(value.decode("latin-1"))
            except (UnicodeDecodeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
    return None


async def _send_413(send: Send, max_bytes: int) -> None:
    """Emit a 413 response using the project's error envelope.

    Args:
        send: ASGI send callable.
        max_bytes: The configured cap, surfaced in the error detail so
            operators can correlate rejections with the setting.
    """
    response = ErrorResponse(
        message="Request body exceeds the configured size limit.",
        errors=[
            ErrorDetail(
                code="REQUEST_BODY_TOO_LARGE",
                message=(
                    f"Request body exceeds the configured maximum of {max_bytes} bytes."
                ),
                field=None,
                details={"max_bytes": max_bytes},
            )
        ],
        status_code=413,
    )
    # ``ErrorResponse`` returns a JSONResponse — drive it through ASGI manually
    # since we are operating below the FastAPI handler layer.
    await response(  # type: ignore[operator]
        {"type": "http", "method": "POST", "headers": []},
        _noop_receive,
        send,
    )


async def _noop_receive() -> Message:
    """No-op receive callable used while emitting the manual 413.

    Returns:
        A synthetic ``http.disconnect`` so the response writer's
        ``listen_for_disconnect`` task doesn't block.
    """
    return {"type": "http.disconnect"}


__all__ = ["ContentLengthLimitMiddleware"]
