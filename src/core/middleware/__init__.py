"""Core middleware + a single ``install_core_middleware`` helper."""

from src.core.middleware.body_limit import ContentLengthLimitMiddleware
from src.core.middleware.exception_logging import ExceptionLoggingMiddleware
from src.core.middleware.rate_limit_headers import RateLimitHeadersMiddleware
from src.core.middleware.request_id import RequestIDMiddleware
from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.core.middleware.security_headers import SecurityHeadersMiddleware
from src.core.middleware.selective_cors import SelectiveCORSMiddleware

__all__ = [
    "ContentLengthLimitMiddleware",
    "ExceptionLoggingMiddleware",
    "RateLimitHeadersMiddleware",
    "RequestIDMiddleware",
    "RequestLoggingMiddleware",
    "SecurityHeadersMiddleware",
    "SelectiveCORSMiddleware",
    "install_core_middleware",
]


def install_core_middleware(
    app,
    *,
    cors_enabled: bool = True,
    cors_excluded_prefixes: list[str] | None = None,
    cors_allow_origins: list[str] | None = None,
    cors_allow_methods: list[str] | None = None,
    cors_allow_headers: list[str] | None = None,
    cors_allow_credentials: bool = False,
    enable_rate_limit_headers: bool = True,
    enable_security_headers: bool = True,
    enable_body_size_limit: bool = True,
) -> None:
    """Wire core middlewares onto a FastAPI app in the correct order.

        Starlette runs middleware outermost-first; ``add_middleware`` prepends,
        so call order here defines outermost-to-innermost. With the full
        stack enabled the request flow is:

            BodyLimit → CORS → SecurityHeaders → RequestID → RequestLogging
            → ExceptionLogging → RateLimitHeaders → handler

        ``ContentLengthLimitMiddleware`` is installed outermost so an
        oversize-body 413 short-circuits before RequestLogging reads the
        body into ``api_logs``. ``SecurityHeadersMiddleware`` sits
        between CORS and RequestID so its headers reach every response,
        including CORS preflights.

        Setting ``cors_enabled=False`` skips ``SelectiveCORSMiddleware`` —
        useful for server-to-server deployments where same-origin browsers
        never call the API.

    Args:
        app: The FastAPI app being configured.
        cors_enabled: When ``False``, ``SelectiveCORSMiddleware`` is not installed.
        cors_excluded_prefixes: Path prefixes excluded from CORS handling.
        cors_allow_origins: Allowed CORS origins.
        cors_allow_methods: Allowed CORS methods.
        cors_allow_headers: Allowed CORS request headers.
        cors_allow_credentials: Whether ``Access-Control-Allow-Credentials`` is sent.
        enable_rate_limit_headers: When ``True`` (default), the rate-limit
            headers middleware surfaces ``X-RateLimit-*`` per request.
        enable_security_headers: When ``True`` (default), attach HSTS /
            nosniff / frame-deny / referrer / permissions / CSP headers
            to every response. Set to ``False`` when an upstream proxy
            already injects equivalent headers.
        enable_body_size_limit: When ``True`` (default), reject inbound
            requests whose body exceeds
            ``CoreSettings.max_request_body_bytes`` with HTTP 413.
    """
    # Innermost first (added last → runs first inside the stack)
    if enable_rate_limit_headers:
        app.add_middleware(RateLimitHeadersMiddleware)
    app.add_middleware(ExceptionLoggingMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    if enable_security_headers:
        app.add_middleware(SecurityHeadersMiddleware)
    if cors_enabled:
        app.add_middleware(
            SelectiveCORSMiddleware,
            excluded_prefixes=cors_excluded_prefixes or [],
            allow_origins=cors_allow_origins or ["*"],
            allow_methods=cors_allow_methods or ["*"],
            allow_headers=cors_allow_headers or ["*"],
            allow_credentials=cors_allow_credentials,
        )
    if enable_body_size_limit:
        # Outermost: oversize bodies should never reach the rest of the
        # stack — they would just bloat the audit log.
        app.add_middleware(ContentLengthLimitMiddleware)
