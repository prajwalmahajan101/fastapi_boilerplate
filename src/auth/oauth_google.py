"""Google OAuth 2.0 sign-in flow + JWT exchange.

Wires Authlib's ``StarletteOAuth2App`` to two FastAPI routes:

* ``GET /api/v1/auth/google/login`` — redirect the browser to
  Google's consent screen.
* ``GET /api/v1/auth/google/callback`` — verify the auth code, upsert
  the local ``User`` row (matched on the verified email), and mint a
  JWT access + refresh pair via :mod:`src.auth.jwt`.

This module is imported only when ``"oauth_google"`` is in
``settings.auth_enabled_providers`` — the Authlib dependency stays
optional for deployments that do not need OAuth.

The :class:`GoogleOAuthProvider` is registered for symmetry with the
other providers, but its ``authenticate`` always returns ``None``: a
per-request authentication scheme is not part of the OAuth flow.
Authenticated browser sessions reach the rest of the API as JWT
bearer tokens — the JWT provider handles them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.base import AuthResult
from src.auth.jwt import mint_token_pair
from src.core.api_log import log_inbound_request
from src.core.db.dependencies import get_session
from src.core.db.transaction import atomic
from src.core.exceptions.auth import AuthenticationFailedError
from src.core.resilience.throttle import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse
from src.core.runtime import get_settings
from src.repository.auth import UserRepository
from src.schema.auth import TokenPair

if TYPE_CHECKING:
    from authlib.integrations.starlette_client import OAuth

    from src.model.auth import User

logger = logging.getLogger(__name__)

_oauth_client: "OAuth | None" = None
_GOOGLE_DISCOVERY = (
    "https://accounts.google.com/.well-known/openid-configuration"
)


def _get_oauth_client() -> "OAuth":
    """Return the lazily-built Authlib OAuth client.

    Raises:
        RuntimeError: When Authlib is not installed.
    """
    global _oauth_client
    if _oauth_client is not None:
        return _oauth_client
    try:
        from authlib.integrations.starlette_client import OAuth  # noqa: PLC0415
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "Authlib is required for the 'oauth_google' provider — install "
            "the runtime dependency or remove 'oauth_google' from "
            "auth_enabled_providers."
        ) from exc

    settings = get_settings()
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_oauth_client_id,
        client_secret=(
            settings.google_oauth_client_secret.get_secret_value()
            if settings.google_oauth_client_secret is not None
            else None
        ),
        server_metadata_url=_GOOGLE_DISCOVERY,
        client_kwargs={"scope": "openid email profile"},
    )
    _oauth_client = oauth
    return oauth


def _check_hosted_domain(claims: dict[str, Any]) -> None:
    """Reject the login when ``hd`` is not in the configured allow-list."""
    allowed = get_settings().google_oauth_allowed_domains
    if not allowed:
        return
    hd = claims.get("hd")
    if hd not in allowed:
        raise AuthenticationFailedError(
            "Google account is not in the allowed domain list."
        )


async def _upsert_user(
    session: AsyncSession, claims: dict[str, Any]
) -> "User":
    """Look up or create the ``User`` row matching the verified email.

    Args:
        session: Request-scoped async session.
        claims: Decoded ID-token claims from Google.

    Returns:
        The persisted, active ``User`` row.

    Raises:
        AuthenticationFailedError: When the email is missing /
            unverified, or the matched user is inactive.
    """
    from src.model.auth import User  # noqa: PLC0415

    email = claims.get("email")
    if not email or not claims.get("email_verified", False):
        raise AuthenticationFailedError(
            "Google account did not return a verified email."
        )

    repo = UserRepository(session)
    user = await repo.get_by_email(email)
    if user is None:
        user = User(
            email=email,
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
        )
        session.add(user)
        await session.flush()
        logger.info("OAuth: created user id=%s for %s", user.id, email)
    elif not user.is_active:
        raise AuthenticationFailedError("User account is disabled.")
    return user


class GoogleOAuthProvider:
    """Marker provider that opts the OAuth routes in via the registry.

    OAuth itself is not a per-request authentication scheme — the
    callback mints a JWT pair which the :class:`JWTProvider` then
    validates on subsequent requests. ``authenticate`` therefore
    always returns ``None``, leaving the registry to fall through to
    the next enabled provider.
    """

    name = "oauth_google"

    async def authenticate(
        self, request: Request, session: AsyncSession
    ) -> AuthResult | None:
        return None


# ── Routes ──────────────────────────────────────────────────────────

oauth_router = APIRouter()


@oauth_router.get(
    "/login",
    summary="Redirect to Google's OAuth consent screen",
    dependencies=[Depends(rate_limit("ip", "30/min"))],
)
@log_inbound_request(service_name="auth_api")
async def google_login(request: Request):
    """Initiate the Google OAuth 2.0 flow.

    Returns:
        A 302 redirect to Google's consent screen.
    """
    oauth = _get_oauth_client()
    redirect_uri = get_settings().google_oauth_redirect_uri
    return await oauth.google.authorize_redirect(request, redirect_uri)


@oauth_router.get(
    "/callback",
    summary="Exchange the OAuth code for a JWT pair",
    response_model=SuccessEnvelope[TokenPair],
    dependencies=[Depends(rate_limit("ip", "30/min"))],
)
@log_inbound_request(service_name="auth_api")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Verify the Google ID token and return a JWT access + refresh pair.

    Raises:
        AuthenticationFailedError: When the upstream callback fails,
            the email is unverified, the hosted domain is not allowed,
            or the matched local user is disabled.
    """
    oauth = _get_oauth_client()
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001
        # Authlib raises a family of errors here; collapsing them is
        # fine because the user-facing message is identical.
        raise AuthenticationFailedError("Google OAuth failed.") from exc

    claims = token.get("userinfo") or {}
    _check_hosted_domain(claims)

    async with atomic(session):
        user = await _upsert_user(session, claims)

    return SuccessResponse(data=mint_token_pair(user.id))


# Self-register at import time so the registry knows about us.
from src.auth import registry as _registry  # noqa: E402

_registry.register(GoogleOAuthProvider())


__all__ = ["GoogleOAuthProvider", "oauth_router"]
