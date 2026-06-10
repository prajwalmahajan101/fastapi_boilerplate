"""JWT bearer-token authentication provider.

Issues and verifies access + refresh tokens signed with the algorithm
configured on :class:`CoreSettings.jwt_algorithm` (HS256 by default,
RS256 supported for asymmetric deployments). Refresh tokens carry a
unique ``jti`` that can be blacklisted via the resilience cache, so
logout is O(1) and survives worker restarts when the cache is backed
by Redis.

Failure modes raise the auth exception family:

* missing / malformed bearer header → ``None`` (registry falls through)
* expired signature → :class:`TokenExpiredError`
* tampered / unknown-issuer / wrong-audience → :class:`TokenInvalidError`
* blacklisted ``jti`` → :class:`TokenRevokedError`

This module imports PyJWT lazily so the boilerplate stays importable
in deployments that never enable the ``"jwt"`` provider.
"""

from __future__ import annotations

import enum
import logging
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.base import AuthResult
from src.core.context import get_request_id
from src.core.exceptions.auth import (
    AuthenticationFailedError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
)
from src.core.metrics import record_counter
from src.core.runtime import get_settings

if TYPE_CHECKING:
    from fastapi import Request

    from src.model.auth import User

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# Back-compat aliases — kept so external callers mid-migration keep
# importing the underscored names without breaking. Prefer the
# unprefixed names in new code.
_ACCESS_TOKEN_TYPE = ACCESS_TOKEN_TYPE
_REFRESH_TOKEN_TYPE = REFRESH_TOKEN_TYPE
_BLACKLIST_PREFIX = "jwt_jti_blacklist:"


def _jwt_module():
    """Import PyJWT lazily; raise a clear error when the dep is absent."""
    try:
        import jwt as _jwt  # noqa: PLC0415

        return _jwt
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "PyJWT is required for the 'jwt' auth provider — install "
            "the runtime dependency or remove 'jwt' from "
            "auth_enabled_providers."
        ) from exc


def _now() -> datetime:
    """Return current UTC time (helper kept private to ease test patching)."""
    return datetime.now(timezone.utc)


def _signing_key() -> str:
    """Return the configured signing key as a plain str.

    Raises:
        RuntimeError: When ``jwt_signing_key`` is unset — should not be
            reachable in practice because ``CoreSettings`` validates
            this at boot when ``"jwt"`` is enabled.
    """
    settings = get_settings()
    secret = settings.jwt_signing_key
    if secret is None:
        raise RuntimeError("jwt_signing_key is not configured.")
    return secret.get_secret_value()


def _common_claims(sub: str, token_type: str) -> dict[str, Any]:
    """Build the issuer / audience / type / jti claims shared by both tokens."""
    settings = get_settings()
    claims: dict[str, Any] = {
        "sub": sub,
        "type": token_type,
        "jti": _secrets.token_urlsafe(16),
    }
    if settings.jwt_issuer:
        claims["iss"] = settings.jwt_issuer
    if settings.jwt_audience:
        claims["aud"] = settings.jwt_audience
    return claims


def mint_access_token(user_id: int | str) -> tuple[str, dict[str, Any]]:
    """Issue a short-lived access token for ``user_id``.

    Args:
        user_id: The user's primary key, encoded as the ``sub`` claim.

    Returns:
        ``(encoded_jwt, claims_dict)`` — the dict is the encoded
        payload, exposed for callers that need the ``jti`` / ``exp``
        without re-decoding.
    """
    jwt = _jwt_module()
    settings = get_settings()
    now = _now()
    claims = _common_claims(str(user_id), _ACCESS_TOKEN_TYPE)
    claims["iat"] = int(now.timestamp())
    claims["exp"] = int(
        (now + timedelta(seconds=settings.jwt_access_ttl_seconds)).timestamp()
    )
    token = jwt.encode(claims, _signing_key(), algorithm=settings.jwt_algorithm)
    return token, claims


def mint_refresh_token(user_id: int | str) -> tuple[str, dict[str, Any]]:
    """Issue a longer-lived refresh token for ``user_id``.

    Args:
        user_id: The user's primary key, encoded as the ``sub`` claim.

    Returns:
        ``(encoded_jwt, claims_dict)``.
    """
    jwt = _jwt_module()
    settings = get_settings()
    now = _now()
    claims = _common_claims(str(user_id), _REFRESH_TOKEN_TYPE)
    claims["iat"] = int(now.timestamp())
    claims["exp"] = int(
        (now + timedelta(seconds=settings.jwt_refresh_ttl_seconds)).timestamp()
    )
    token = jwt.encode(claims, _signing_key(), algorithm=settings.jwt_algorithm)
    return token, claims


def mint_token_pair(user_id: int | str) -> dict[str, Any]:
    """Return both tokens + metadata as a JSON-friendly dict.

    Returns:
        ``{"access_token": ..., "refresh_token": ...,
        "token_type": "bearer", "expires_in": <access_ttl_seconds>}``.
    """
    access, _ = mint_access_token(user_id)
    refresh, _ = mint_refresh_token(user_id)
    settings = get_settings()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_ttl_seconds,
    }


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """Verify the signature + standard claims and return the payload.

    Args:
        token: Raw JWT string (no ``Bearer`` prefix).
        expected_type: When set, the decoded ``type`` claim must match.

    Returns:
        The decoded claim dict.

    Raises:
        TokenExpiredError: Signature is valid but ``exp`` has elapsed.
        TokenInvalidError: Signature / issuer / audience / type mismatch.
    """
    jwt = _jwt_module()
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            _signing_key(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["exp", "iat", "sub", "type", "jti"],
                "verify_aud": settings.jwt_audience is not None,
                "verify_iss": settings.jwt_issuer is not None,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError(str(exc) or "Invalid token.") from exc

    if expected_type is not None and payload.get("type") != expected_type:
        raise TokenInvalidError(
            f"Expected {expected_type} token, got {payload.get('type')!r}."
        )
    return payload


class BlacklistOutcome(enum.Enum):
    """Three-state result of a blacklist lookup.

    ``LISTED`` and ``NOT_LISTED`` mean the cache responded. ``UNAVAILABLE``
    means the cache backend errored — callers decide whether to fail
    open (access path, short-lived) or fail closed (refresh path,
    long-lived).
    """

    LISTED = "listed"
    NOT_LISTED = "not_listed"
    UNAVAILABLE = "unavailable"


async def check_blacklist(
    jti: str, *, sub: str | None = None, token_type: str = ACCESS_TOKEN_TYPE
) -> BlacklistOutcome:
    """Look up ``jti`` in the blacklist cache; return the three-state outcome.

    On a cache backend error this logs a WARNING with ``jti`` / ``sub``
    / ``request_id`` / ``token_type`` and bumps the
    ``auth_blacklist_unreachable`` counter so operators see Redis blips
    without having to grep. The hybrid fail policy itself (open for
    access, closed for refresh) is implemented by the callers — this
    function just reports.

    Args:
        jti: The token's ``jti`` claim.
        sub: The token's ``sub`` claim (user id) — included in the
            outage WARNING for incident triage.
        token_type: ``"access"`` or ``"refresh"`` — labels the metric
            so spikes on the refresh path stand out.

    Returns:
        :class:`BlacklistOutcome` — ``LISTED``, ``NOT_LISTED``, or
        ``UNAVAILABLE``.
    """
    from resilience_kit.cache.provider import get_cache  # noqa: PLC0415

    alias = get_settings().jwt_blacklist_cache_alias
    try:
        cache = await get_cache(alias)
        hit = await cache.get(_BLACKLIST_PREFIX + jti)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "JWT blacklist lookup unavailable (%s).",
            exc,
            extra={
                "event": "auth_blacklist_unreachable",
                "jti": jti,
                "sub": sub,
                "token_type": token_type,
                "request_id": get_request_id(),
            },
        )
        # ``token_type`` lives in the event name (not a metric label) to
        # respect the bounded-cardinality contract on ``record_counter``.
        record_counter(f"auth_blacklist_unreachable_{token_type}", status="error")
        return BlacklistOutcome.UNAVAILABLE
    return BlacklistOutcome.LISTED if hit is not None else BlacklistOutcome.NOT_LISTED


async def _is_blacklisted(jti: str) -> bool:
    """Back-compat shim: ``True`` when the cache says ``jti`` is revoked.

    Treats ``UNAVAILABLE`` as not blacklisted (fail-open) for the
    access-token path. Refresh-token call sites must use
    :func:`_check_blacklist` directly so they can fail closed on
    ``UNAVAILABLE``.
    """
    outcome = await check_blacklist(jti, token_type=ACCESS_TOKEN_TYPE)
    return outcome is BlacklistOutcome.LISTED


# Back-compat alias for callers that mid-migrated to the underscore name.
_check_blacklist = check_blacklist


async def blacklist_jti(jti: str, *, ttl_seconds: int | None = None) -> None:
    """Mark ``jti`` as logged-out for ``ttl_seconds``.

    Args:
        jti: The ``jti`` claim of the refresh token being revoked.
        ttl_seconds: Cache TTL — defaults to the refresh-token TTL so
            the entry expires when the underlying token would have.
    """
    from resilience_kit.cache.provider import get_cache  # noqa: PLC0415

    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_refresh_ttl_seconds
    try:
        cache = await get_cache(settings.jwt_blacklist_cache_alias)
        await cache.set(_BLACKLIST_PREFIX + jti, "1", ttl=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("JWT blacklist write failed (%s).", exc)


async def load_active_user(session: AsyncSession, sub: str) -> "User | None":
    """Resolve a ``sub`` claim to an active ``User`` ORM row."""
    from src.model.auth import User  # noqa: PLC0415

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    stmt = select(User).where(User.id == user_id, User.is_active.is_(True)).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# Back-compat alias.
_load_active_user = load_active_user


class JWTProvider:
    """Bearer-token :class:`AuthProvider` implementation."""

    name = "jwt"

    async def authenticate(
        self, request: "Request", session: AsyncSession
    ) -> AuthResult | None:
        """Validate ``Authorization: Bearer <token>`` and resolve the user.

        Returns ``None`` when no bearer header is present so the
        registry can fall through. Any malformed / expired / revoked
        token raises the matching auth exception.
        """
        header = request.headers.get("authorization")
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None

        payload = decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
        jti = payload.get("jti")
        if jti:
            # Access path: short-lived. UNAVAILABLE falls through to
            # allow so a Redis blip does not lock every authed call out;
            # the metric + WARNING emitted by check_blacklist make the
            # blip visible to operators.
            outcome = await check_blacklist(
                jti, sub=payload.get("sub"), token_type=ACCESS_TOKEN_TYPE
            )
            if outcome is BlacklistOutcome.LISTED:
                raise TokenRevokedError()

        user = await load_active_user(session, payload["sub"])
        if user is None:
            raise AuthenticationFailedError("User account is disabled.")

        request.state.jwt_claims = payload
        return AuthResult(user=user, provider=self.name, token_claims=payload)


# Self-register at import time so the registry picks us up.
from src.auth import registry as _registry  # noqa: E402

_registry.register(JWTProvider())


__all__ = [
    "ACCESS_TOKEN_TYPE",
    "BlacklistOutcome",
    "JWTProvider",
    "REFRESH_TOKEN_TYPE",
    "blacklist_jti",
    "check_blacklist",
    "decode_token",
    "load_active_user",
    "mint_access_token",
    "mint_refresh_token",
    "mint_token_pair",
]
