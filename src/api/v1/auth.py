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
from resilience_kit.adapters.fastapi import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse
from src.repository.auth import APIKeyRepository
from src.schema.auth import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyRead,
    UserRead,
)
from src.service.auth import APIKeyService

router = APIRouter()


_AUTH_RESPONSES = {**DEFAULT_RESPONSES, **RESPONSES_UNAUTHORIZED, **RESPONSES_FORBIDDEN}


def _user_read(user) -> UserRead:
    """Build the outbound ``UserRead`` from a ``User`` ORM row.

    Returns the pydantic model directly — ``SuccessResponse`` serialises
    the envelope through pydantic in one pass via ``model_dump(mode="json")``,
    so calling ``.model_dump()`` here would just be a redundant round-trip
    (see ISSUE-028 and ``src/api/CLAUDE.md`` "Common pitfalls").
    """
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        timezone=user.timezone,
        is_active=user.is_active,
        roles=[r.name for r in user.roles or []],
    )


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
    data = [APIKeyRead.model_validate(r) for r in rows]
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
        api_key, raw_key = await service.create_for_user(user=user, name=payload.name)
    data = APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        is_active=api_key.is_active,
        key=raw_key,
    )
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
        revoked_now, already = await service.revoke(api_key_id=api_key_id, user=user)
    if not revoked_now and not already:
        from src.core.exceptions.repository import EntityNotFoundError

        raise EntityNotFoundError("APIKey", api_key_id)
    return SuccessResponse(
        message="API key revoked." if revoked_now else "API key was already revoked."
    )


__all__ = ["router"]
