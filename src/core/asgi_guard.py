"""Bearer-token ASGI guard for raw sub-app mounts.

The Prometheus text-exposition endpoint is a raw ASGI mount
(``prometheus_client.make_asgi_app``), so it bypasses the FastAPI
dependency system the rest of the app uses to gate privileged routes.
:class:`BearerTokenGuard` wraps such a mount and requires a shared-secret
``Authorization: Bearer <token>`` header, answering unauthenticated
requests with ``401`` before the inner app ever runs. Comparison is
constant-time (:func:`hmac.compare_digest`) so it does not leak the token
length or a matching prefix through timing.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import cast

# Minimal ASGI type aliases — avoids a dependency on ``asgiref`` just for
# annotations. An ASGI app is a callable of ``(scope, receive, send)``.
Scope = dict[str, object]
Message = dict[str, object]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BearerTokenGuard:
    """Wrap an ASGI app, requiring ``Authorization: Bearer <token>``.

    Non-HTTP scopes (e.g. ``lifespan``) pass straight through so the inner
    app's startup/shutdown still runs. HTTP requests without a matching
    bearer token get a bare ``401`` and the inner app is not invoked.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        """Bind the guard to ``app``.

        Args:
            app: The inner ASGI application to protect.
            token: The shared secret a caller must present as
                ``Authorization: Bearer <token>``.

        Raises:
            ValueError: If ``token`` is empty — an empty secret would gate
                nothing, so construction fails loudly rather than shipping
                an effectively-open endpoint.
        """
        if not token:
            raise ValueError("BearerTokenGuard requires a non-empty token")
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Authorize the request, then delegate to the inner app.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        raw_headers = cast(
            "list[tuple[bytes, bytes]]", scope.get("headers") or []
        )
        headers = dict(raw_headers)
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if hmac.compare_digest(provided, self._expected):
            await self._app(scope, receive, send)
            return

        await self._send_unauthorized(send)

    @staticmethod
    async def _send_unauthorized(send: Send) -> None:
        """Emit a bare ``401`` response with a ``WWW-Authenticate`` hint."""
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"Unauthorized"})
