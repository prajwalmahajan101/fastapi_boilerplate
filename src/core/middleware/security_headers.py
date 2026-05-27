"""``SecurityHeadersMiddleware`` — defensive response headers for every reply.

This is a JSON-only s2s API; browsers should never receive a meaningful
response from it. The headers below are belt-and-braces protection in
case a misconfig ever exposes us:

* ``Strict-Transport-Security`` — tell any browser that does reach us
  to pin HTTPS for a year. Suppressed in dev/local so a HSTS pin can't
  trap a developer hitting ``http://localhost``.
* ``X-Content-Type-Options: nosniff`` — block MIME sniffing on
  JSON responses.
* ``X-Frame-Options: DENY`` — paired with the CSP ``frame-ancestors``
  directive; reject every frame embed.
* ``Referrer-Policy`` — clamp cross-origin referer leakage on any
  navigation that does happen to chain off our responses.
* ``Permissions-Policy`` — deny browser feature use across the board.
* ``Content-Security-Policy: default-src 'none'; frame-ancestors 'none'``
  — the API serves no HTML and no embedded resources; the strictest
  policy is correct.

Toggle via ``CoreSettings.security_headers_enabled`` (default ``True``).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.runtime import get_settings

_DEV_ENVIRONMENTS = {"dev", "development", "test", "local"}

_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    ),
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

_HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

# Swagger UI / ReDoc HTML pulls its bundle from jsDelivr and the FastAPI
# favicon from tiangolo.com. The base ``default-src 'none'`` policy blocks
# both, so the rendered page silently fails to load. We swap in a CSP that
# whitelists those origins (and the page's inline init script) for the docs
# paths only — every JSON / API route still gets the strict default above.
_DOCS_PATH_PREFIXES: tuple[str, ...] = ("/docs", "/redoc")
_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' https://fastapi.tiangolo.com https://cdn.jsdelivr.net data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a fixed defensive header set to every outbound response."""

    async def dispatch(self, request: Request, call_next):
        """Run the next handler and stamp the security headers onto the response.

        ``Strict-Transport-Security`` is skipped in dev-like environments
        so a developer pointed at ``http://localhost`` doesn't accidentally
        get their browser to refuse plaintext for a year.

        Args:
            request: Incoming Starlette request (unused besides the
                environment check on settings).
            call_next: Callable that runs the next ASGI handler.

        Returns:
            The downstream response with the security headers merged in.
        """
        response: Response = await call_next(request)
        # Set the docs-relaxed CSP first so the strict default in the loop
        # below (which uses ``setdefault``) doesn't clobber it.
        if request.url.path.startswith(_DOCS_PATH_PREFIXES):
            response.headers["Content-Security-Policy"] = _DOCS_CSP
        for name, value in _BASE_HEADERS.items():
            response.headers.setdefault(name, value)
        environment = get_settings().app_environment.strip().lower()
        if environment not in _DEV_ENVIRONMENTS:
            response.headers.setdefault(*_HSTS_HEADER)
        return response


__all__ = ["SecurityHeadersMiddleware"]
