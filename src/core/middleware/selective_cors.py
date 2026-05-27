"""``SelectiveCORSMiddleware`` — CORS that skips configured path prefixes.

Some endpoints (server-to-server webhooks, internal callbacks) must
appear *invisible* to browser CORS preflights, while the public API needs
permissive CORS. This middleware wraps Starlette's standard
``CORSMiddleware`` and short-circuits when the request path begins with
any ``excluded_prefixes`` entry.
"""

from __future__ import annotations

from typing import Iterable

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class SelectiveCORSMiddleware:
    """CORS, except for paths starting with one of ``excluded_prefixes``."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        excluded_prefixes: Iterable[str] = (),
        allow_origins: Iterable[str] = ("*",),
        allow_methods: Iterable[str] = ("*",),
        allow_headers: Iterable[str] = ("*",),
        allow_credentials: bool = False,
        expose_headers: Iterable[str] = (),
        max_age: int = 600,
    ) -> None:
        """Wrap ``app`` with Starlette CORS, recording the bypass prefixes.

        Args:
            app: The downstream ASGI app being wrapped.
            excluded_prefixes: Path prefixes that bypass CORS entirely.
            allow_origins: ``Access-Control-Allow-Origin`` values.
            allow_methods: ``Access-Control-Allow-Methods`` values.
            allow_headers: ``Access-Control-Allow-Headers`` values.
            allow_credentials: Whether the ``Allow-Credentials`` header
                is sent (incompatible with ``allow_origins=["*"]``).
            expose_headers: ``Access-Control-Expose-Headers`` values.
            max_age: ``Access-Control-Max-Age`` in seconds.
        """
        self.app = app
        self.excluded_prefixes = tuple(excluded_prefixes)
        self.cors = CORSMiddleware(
            app,
            allow_origins=list(allow_origins),
            allow_methods=list(allow_methods),
            allow_headers=list(allow_headers),
            allow_credentials=allow_credentials,
            expose_headers=list(expose_headers),
            max_age=max_age,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route the request: bypass CORS for excluded prefixes, else delegate.

        Args:
            scope: ASGI scope dict.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            if any(path.startswith(p) for p in self.excluded_prefixes):
                await self.app(scope, receive, send)
                return
        await self.cors(scope, receive, send)
