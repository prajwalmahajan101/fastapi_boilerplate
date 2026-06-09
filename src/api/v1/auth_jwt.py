"""JWT-specific endpoints — ``/token/refresh`` + ``/logout``.

Split out from ``src/api/v1/auth.py`` so the module-top imports of
``src.auth.jwt`` (and therefore PyJWT) can stay where they belong
without forcing the import on deployments that do not enable the
``"jwt"`` provider. ``v1/__init__.py`` mounts this router only when
``"jwt"`` is in ``settings.auth_enabled_providers``.

* ``POST /api/v1/auth/token/refresh`` — rotate the refresh token, mint
  a fresh access+refresh pair, blacklist the old ``jti``.
* ``POST /api/v1/auth/logout`` — blacklist the supplied refresh token
  (idempotent; treats already-expired / already-blacklisted as success).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt import (
    REFRESH_TOKEN_TYPE,
    BlacklistOutcome,
    blacklist_jti,
    check_blacklist,
    decode_token,
    load_active_user,
    mint_token_pair,
)
from src.common.openapi_metadata import DEFAULT_RESPONSES, RESPONSES_UNAUTHORIZED
from src.core.api_log import log_inbound_request
from src.core.db.dependencies import get_session
from src.core.exceptions.auth import (
    AuthenticationFailedError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
)
from src.core.resilience.throttle import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse
from src.schema.auth import TokenLogoutRequest, TokenPair, TokenRefreshRequest

jwt_router = APIRouter()


@jwt_router.post(
    "/token/refresh",
    summary="Exchange a refresh token for a new access pair",
    response_model=SuccessEnvelope[TokenPair],
    dependencies=[Depends(rate_limit("auth", "5/min"))],
    responses={**DEFAULT_RESPONSES, **RESPONSES_UNAUTHORIZED},
)
@log_inbound_request(service_name="auth_api")
async def refresh_token(
    request: Request,
    payload: TokenRefreshRequest,
    session: AsyncSession = Depends(get_session),
):
    """Validate the refresh token and mint a fresh ``(access, refresh)`` pair.

    The old refresh token's ``jti`` is blacklisted so it cannot be
    replayed (refresh-token rotation). Clients should store the new
    pair and discard the old one immediately.

    Raises:
        TokenExpiredError: Refresh token signature is valid but expired.
        TokenInvalidError: Signature / issuer / audience / type mismatch.
        TokenRevokedError: Refresh token's ``jti`` is already blacklisted,
            or the blacklist cache was unavailable (fail-closed).
    """
    claims = decode_token(payload.refresh_token, expected_type=REFRESH_TOKEN_TYPE)
    jti = claims.get("jti")
    if jti:
        # Refresh path fails *closed* on cache outage: a long-lived
        # refresh token replayed during a Redis blip would otherwise
        # mint a fresh access+refresh pair, defeating logout. The
        # blacklist lookup's WARNING + metric tells operators why.
        outcome = await check_blacklist(
            jti, sub=claims.get("sub"), token_type=REFRESH_TOKEN_TYPE
        )
        if outcome is not BlacklistOutcome.NOT_LISTED:
            raise TokenRevokedError()

    user = await load_active_user(session, claims["sub"])
    if user is None:
        raise AuthenticationFailedError("User account is disabled.")

    # Rotation: blacklist the old refresh-token jti before minting new
    # tokens so a concurrent replay attempt sees the rejection.
    if jti:
        await blacklist_jti(jti)

    return SuccessResponse(data=mint_token_pair(user.id))


@jwt_router.post(
    "/logout",
    summary="Revoke a refresh token",
    response_model=SuccessEnvelope[None],
    dependencies=[Depends(rate_limit("auth", "5/min"))],
    responses={**DEFAULT_RESPONSES, **RESPONSES_UNAUTHORIZED},
)
@log_inbound_request(service_name="auth_api")
async def logout(
    request: Request,
    payload: TokenLogoutRequest,
):
    """Blacklist the supplied refresh token's ``jti``. Idempotent.

    Returns 200 even when the token is already expired / blacklisted —
    logout is best-effort and clients should treat success as
    "credential gone". The corresponding access token continues to
    work until ``jwt_access_ttl_seconds`` elapses; keep that TTL short
    if instant revocation matters.
    """
    try:
        claims = decode_token(payload.refresh_token, expected_type=REFRESH_TOKEN_TYPE)
    except (TokenExpiredError, TokenInvalidError):
        # Already unusable — treat as already-logged-out.
        return SuccessResponse(message="Logged out.")

    jti = claims.get("jti")
    if jti:
        await blacklist_jti(jti)
    return SuccessResponse(message="Logged out.")


__all__ = ["jwt_router"]
