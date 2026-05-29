"""Pydantic DTOs for the auth surface (``/me`` + ``/api-keys``).

The inbound shapes validate request bodies; the outbound shapes flow
into the response envelope's ``data`` field. ``APIKeyCreated`` is
deliberately distinct from ``APIKeyRead`` — the **raw key** is only
ever returned at creation time and never persisted in plaintext.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.core.base.schema import BaseSchema


class APIKeyCreate(BaseSchema):
    """Request body — create a new API key for the calling user."""

    name: str = Field(..., min_length=1, max_length=255)


class APIKeyRead(BaseSchema):
    """Response shape — listing / fetching API keys (no secret)."""

    id: int
    name: str
    prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    is_active: bool


class APIKeyCreated(APIKeyRead):
    """Response for ``POST /api-keys`` — includes the raw key.

    The ``key`` field is only present on the creation response. Clients
    must store it themselves; the server cannot recover it.
    """

    key: str


class UserRead(BaseSchema):
    """Response shape — the authenticated user (``/me``)."""

    id: int
    email: str
    full_name: str
    timezone: str
    is_active: bool
    roles: list[str]


class TokenRefreshRequest(BaseSchema):
    """Request body — exchange a refresh token for a fresh access pair."""

    refresh_token: str = Field(..., min_length=1)


class TokenLogoutRequest(BaseSchema):
    """Request body — revoke a refresh token's ``jti`` (logout)."""

    refresh_token: str = Field(..., min_length=1)


class TokenPair(BaseSchema):
    """Response shape — newly minted JWT access + refresh tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


__all__ = [
    "APIKeyCreate",
    "APIKeyCreated",
    "APIKeyRead",
    "TokenLogoutRequest",
    "TokenPair",
    "TokenRefreshRequest",
    "UserRead",
]
