"""Authentication / account endpoints — ``/me`` and ``/api-keys``.

* ``GET /me`` — return the authenticated user's profile.
* ``GET /api-keys`` — list every key (active + revoked) owned by the
  caller.
* ``POST /api-keys`` — issue a fresh key. The raw key is returned
  exactly once; the server stores only the encrypted form.
* ``POST /api-keys/{pk}/revoke`` — soft-revoke a key. Authentication
  rejects the revoked key on its next use; the audit row is preserved.

Every route uses ``Depends(RequireResource(Resource.API_KEY, ...))``
to enforce RBAC. The ``RequireResource`` instance also returns the
authenticated user, so handlers re-use it via ``Depends`` instead of
adding a second ``current_user`` dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.enums import Action, Resource
from src.common.openapi_metadata import (
    DEFAULT_RESPONSES,
    RESPONSES_FORBIDDEN,
    RESPONSES_NOT_FOUND,
    RESPONSES_UNAUTHORIZED,
)
from src.core.api_log import log_inbound_request
from src.core.db.dependencies import get_session
from src.core.db.transaction import atomic
from src.core.rbac import RequireResource
from src.core.resilience.throttle import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse
from src.repository.auth import APIKeyRepository
from src.schema.auth import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyRead,
    TokenLogoutRequest,
    TokenPair,
    TokenRefreshRequest,
    UserRead,
)
from src.service.auth import APIKeyService

router = APIRouter()
#: JWT-specific endpoints (refresh / logout). Mounted by
#: ``v1/__init__.py`` only when ``"jwt"`` is in
#: ``auth_enabled_providers`` so deployments that skip JWT do not
#: advertise the routes in their OpenAPI surface.
jwt_router = APIRouter()


_AUTH_RESPONSES = {**DEFAULT_RESPONSES, **RESPONSES_UNAUTHORIZED, **RESPONSES_FORBIDDEN}


def _user_read(user) -> dict:
    """Build the outbound ``UserRead`` shape from a ``User`` ORM row."""
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        timezone=user.timezone,
        is_active=user.is_active,
        roles=[r.name for r in user.roles or []],
    ).model_dump()


@router.get(
    "/me",
    summary="Get the authenticated user",
    response_model=SuccessEnvelope[UserRead],
    dependencies=[Depends(rate_limit("endpoint", "120/min"))],
    responses=_AUTH_RESPONSES,
)
@log_inbound_request(service_name="auth_api")
async def me(
    request: Request,
    user=Depends(RequireResource(Resource.ACCOUNT, Action.READ)),
):
    """Return the profile of the user owning the inbound API key.

    Returns:
        Success envelope carrying the :class:`UserRead`.
    """
    return SuccessResponse(data=_user_read(user))


@router.get(
    "/api-keys",
    summary="List my API keys",
    response_model=SuccessEnvelope[list[APIKeyRead]],
    dependencies=[Depends(rate_limit("endpoint", "60/min"))],
    responses=_AUTH_RESPONSES,
)
@log_inbound_request(service_name="auth_api")
async def list_api_keys(
    request: Request,
    user=Depends(RequireResource(Resource.API_KEY, Action.READ)),
    session: AsyncSession = Depends(get_session),
):
    """Return every active API key owned by the caller (revoked + live)."""
    repo = APIKeyRepository(session)
    rows = await repo.list_for_user(user.id)
    data = [APIKeyRead.model_validate(r).model_dump() for r in rows]
    return SuccessResponse(data=data)


@router.post(
    "/api-keys",
    summary="Issue a new API key",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[APIKeyCreated],
    dependencies=[Depends(rate_limit("endpoint", "10/min"))],
    responses=_AUTH_RESPONSES,
)
@log_inbound_request(service_name="auth_api")
async def create_api_key(
    request: Request,
    payload: APIKeyCreate,
    user=Depends(RequireResource(Resource.API_KEY, Action.CREATE)),
    session: AsyncSession = Depends(get_session),
):
    """Mint a new API key for the caller. The raw key is returned **once**."""
    service = APIKeyService(session)
    async with atomic(session):
        api_key, raw_key = await service.create_for_user(
            user=user, name=payload.name
        )
    data = APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        is_active=api_key.is_active,
        key=raw_key,
    ).model_dump()
    return SuccessResponse(
        data=data,
        message="API key created. Store the `key` field now — it cannot be retrieved again.",
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/api-keys/{api_key_id}/revoke",
    summary="Revoke an API key",
    response_model=SuccessEnvelope[None],
    dependencies=[Depends(rate_limit("endpoint", "30/min"))],
    responses={**_AUTH_RESPONSES, **RESPONSES_NOT_FOUND},
)
@log_inbound_request(service_name="auth_api")
async def revoke_api_key(
    request: Request,
    api_key_id: int,
    user=Depends(RequireResource(Resource.API_KEY, Action.UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """Soft-revoke a key. Idempotent.

    Raises:
        EntityNotFoundError: When ``api_key_id`` is not an active key
            owned by the caller (404).
    """
    service = APIKeyService(session)
    async with atomic(session):
        revoked_now, already = await service.revoke(
            api_key_id=api_key_id, user=user
        )
    if not revoked_now and not already:
        from src.core.exceptions.repository import EntityNotFoundError

        raise EntityNotFoundError("APIKey", api_key_id)
    return SuccessResponse(
        message="API key revoked." if revoked_now else "API key was already revoked."
    )


# ── JWT refresh + logout ─────────────────────────────────────────────


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
    """Validate the supplied refresh token and mint a fresh ``(access, refresh)`` pair.

    The old refresh token's ``jti`` is blacklisted so it cannot be
    replayed (refresh-token rotation). Clients should store the new
    pair and discard the old one immediately.

    Raises:
        TokenExpiredError: Refresh token signature is valid but expired.
        TokenInvalidError: Signature / issuer / audience / type mismatch.
        TokenRevokedError: Refresh token's ``jti`` is already blacklisted.
    """
    from src.auth.jwt import (  # noqa: PLC0415
        REFRESH_TOKEN_TYPE,
        BlacklistOutcome,
        blacklist_jti,
        check_blacklist,
        decode_token,
        load_active_user,
        mint_token_pair,
    )
    from src.core.exceptions.auth import (  # noqa: PLC0415
        AuthenticationFailedError,
        TokenRevokedError,
    )

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
    from src.auth.jwt import (  # noqa: PLC0415
        REFRESH_TOKEN_TYPE,
        blacklist_jti,
        decode_token,
    )
    from src.core.exceptions.auth import (  # noqa: PLC0415
        TokenExpiredError,
        TokenInvalidError,
    )

    try:
        claims = decode_token(
            payload.refresh_token, expected_type=REFRESH_TOKEN_TYPE
        )
    except (TokenExpiredError, TokenInvalidError):
        # Already unusable — treat as already-logged-out.
        return SuccessResponse(message="Logged out.")

    jti = claims.get("jti")
    if jti:
        await blacklist_jti(jti)
    return SuccessResponse(message="Logged out.")


__all__ = ["jwt_router", "router"]
