"""``RequestIDMiddleware`` — extract or mint an X-Request-ID and bind to ContextVar."""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.context import clear_request_context, set_request_context

_RID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Set ``request.state.request_id`` + bind ContextVar + echo ``X-Request-ID`` header."""

    async def dispatch(self, request: Request, call_next):
        """Bind a request id to the call, stamp it on the response header.

        Incoming ``X-Request-ID`` is accepted when it matches the
        allowed character set; otherwise a fresh UUID4 hex is minted
        so downstream logs / audit rows can correlate.

        Args:
            request: Incoming Starlette request.
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The response with ``X-Request-ID`` set.
        """
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _RID_PATTERN.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        token = set_request_context(rid)
        try:
            response: Response = await call_next(request)
        finally:
            clear_request_context(token)
        response.headers["X-Request-ID"] = rid
        return response
