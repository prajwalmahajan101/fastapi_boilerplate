"""Request context — async-safe request ID tracking via ContextVar.

The ID is set by ``RequestIDMiddleware`` at the start of each request and
read by the logging filter so every log record carries the same correlation
ID. ContextVar copies on each ``asyncio.Task``, so concurrent requests do
not bleed IDs into each other.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_context(request_id: str | None) -> Token[str | None]:
    """Bind a request ID to the current context; returns a reset token.

    Args:
        request_id: The request ID to bind (or ``None`` to clear).

    Returns:
        A token that can later be passed to ``clear_request_context``.
    """
    return request_id_ctx.set(request_id)


def clear_request_context(token: Token[str | None]) -> None:
    """Reset the request ID using a token returned by ``set_request_context``.

    Args:
        token: The token returned by the corresponding ``set_request_context``.
    """
    request_id_ctx.reset(token)


def get_request_id() -> str | None:
    """Read the request ID bound to the current context (or None).

    Returns:
        The request ID, or ``None`` if none has been bound.
    """
    return request_id_ctx.get(None)
