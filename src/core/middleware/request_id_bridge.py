"""Mirror the kit's ``request_id`` ContextVar into the boilerplate's.

The kit's :class:`resilience_kit.middleware.request_id.RequestIDMiddleware`
writes into ``resilience_kit.context.request_id``; this repo's response
envelope, structured logging filter, ``BaseCustomError`` capture, and
api-log dispatch all read from :data:`src.core.context.request_id_ctx`.
Without a bridge the two ContextVars stay disconnected and every
boilerplate-shaped log line / envelope / audit row emits ``request_id``
as ``None``.

Install this middleware **inside** the kit's stack (i.e. add it before
``install_middleware_stack`` so Starlette's prepend semantics make it
the inner layer). Then the kit's RequestIDMiddleware has already set
its own contextvar by the time :func:`bind_to` runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from resilience_kit.context import bind_to

from src.core.context import request_id_ctx

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class RequestIdBridgeMiddleware:
    """ASGI shim that copies the kit's request_id into ours per request."""

    def __init__(self, app: ASGIApp) -> None:
        """Capture the inner ASGI app the middleware wraps.

        Args:
            app: The next ASGI app in the middleware chain.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the kit's request_id into ``request_id_ctx`` for the request.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with bind_to(request_id_ctx):
            await self.app(scope, receive, send)


__all__ = ["RequestIdBridgeMiddleware"]
