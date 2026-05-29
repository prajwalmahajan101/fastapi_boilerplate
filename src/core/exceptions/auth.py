"""Auth + RBAC exception family.

Three concrete exceptions:

* :class:`AuthenticationFailedError` — the inbound credentials are
  absent or invalid. HTTP 401.
* :class:`APIKeyRevokedError` — credentials are valid but the API key
  has been soft-revoked. HTTP 401 (same as the failure case so a
  revoked key cannot be enumerated apart from a wrong key).
* :class:`PermissionDeniedError` — credentials are valid but the
  principal does not hold the required ``(resource, action)`` pair.
  HTTP 403.

Registered with the exception → HTTP-status mapping in
``src/core/exceptions/handlers.py`` so routes raising any of these get
the envelope shape automatically.
"""

from __future__ import annotations

from src.core.base.exception import BaseCustomError


class AuthenticationFailedError(BaseCustomError):
    """Credentials missing or invalid."""

    default_message = "Authentication failed."
    error_code = "AUTHENTICATION_FAILED"
    status_code = 401


class APIKeyRevokedError(AuthenticationFailedError):
    """The provided API key has been revoked.

    Returns 401 — same as ``AuthenticationFailedError`` — so a revoked
    key is indistinguishable from an unknown one from the caller's
    perspective. The distinction lives in the audit log only.
    """

    default_message = "API key has been revoked."
    error_code = "API_KEY_REVOKED"


class PermissionDeniedError(BaseCustomError):
    """Authenticated principal lacks the required permission."""

    default_message = "Permission denied."
    error_code = "PERMISSION_DENIED"
    status_code = 403


class TokenExpiredError(AuthenticationFailedError):
    """The supplied JWT signature is valid but ``exp`` has elapsed.

    Returns 401. Distinct error_code from
    :class:`AuthenticationFailedError` so clients can tell
    "refresh the token" apart from "credentials wrong".
    """

    default_message = "Token has expired."
    error_code = "TOKEN_EXPIRED"


class TokenInvalidError(AuthenticationFailedError):
    """The supplied JWT failed signature / issuer / audience verification."""

    default_message = "Token is invalid."
    error_code = "TOKEN_INVALID"


class TokenRevokedError(AuthenticationFailedError):
    """The supplied JWT's ``jti`` is blacklisted (post-logout reuse)."""

    default_message = "Token has been revoked."
    error_code = "TOKEN_REVOKED"


__all__ = [
    "APIKeyRevokedError",
    "AuthenticationFailedError",
    "PermissionDeniedError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenRevokedError",
]
