# Authentication

The boilerplate ships a **pluggable provider registry**: deployments
choose which auth schemes are active by listing provider names in
`settings.auth_enabled_providers`. The composite `current_user` /
`current_user_optional` dependencies walk the enabled list in order
and the first provider that returns an `AuthResult` wins.

```
src/auth/
  base.py          # AuthProvider Protocol + AuthResult dataclass
  registry.py      # ordered registry + composite dependencies
  api_key.py       # X-API-Key  (APIKeyProvider)
  jwt.py           # JWT bearer (JWTProvider)
  oauth_google.py  # Google OAuth + JWT mint (GoogleOAuthProvider)
```

Routes do not change shape — they always depend on `current_user`:

```python
from src.auth import current_user

@router.get("/me")
async def me(user = Depends(current_user)):
    ...
```

## Providers

### `api_key`

`X-API-Key: <token>` against the `APIKey` table. Lookup uses the
8-char prefix + constant-time compare; `last_used_at` is debounced
via the resilience cache so a busy key does not generate one UPDATE
per request. **Default** — enabled out of the box.

### `jwt`

`Authorization: Bearer <token>` — HS256 (default) or RS256. Tokens
are minted by `src.auth.jwt.mint_token_pair(user_id)`; verification
runs through `decode_token`.

Refresh-token rotation is logout-aware: each refresh token carries a
unique `jti`, and the `/auth/token/refresh` endpoint blacklists the
old `jti` in the resilience cache before issuing the new pair. The
`/auth/logout` endpoint blacklists the supplied refresh `jti` so
post-logout reuse is rejected for the remainder of the refresh TTL.

Routes mounted only when `"jwt"` is enabled:

| Route | Purpose |
|---|---|
| `POST /api/v1/auth/token/refresh` | Rotate the refresh token, mint a new pair. |
| `POST /api/v1/auth/logout` | Blacklist the refresh token's `jti`. |

### `oauth_google`

Authlib-backed Google OAuth 2.0 flow. The callback verifies the ID
token, upserts the local `User` row matched on the verified email,
and mints a JWT pair. The provider's `authenticate()` returns `None`
on every request — OAuth is a *minting* path, not a per-request
scheme. Subsequent requests authenticate via the `jwt` provider.

Routes mounted only when `"oauth_google"` is enabled:

| Route | Purpose |
|---|---|
| `GET /api/v1/auth/google/login` | Redirect to Google's consent screen. |
| `GET /api/v1/auth/google/callback` | Exchange the code → mint JWT pair. |

Optional hosted-domain allow-list: `google_oauth_allowed_domains`.

#### Default roles on first sign-in

A brand-new OAuth user is auto-attached to every `Role` row flagged
`is_default=True` inside the same transaction as the user insert (so a
role-attach failure rolls the user back — a partially-provisioned
account is never observable). Returning users are left alone; this
fires only on first sign-in.

Seed at least one default role in your bootstrap migration or seed
script, e.g.:

```python
session.add(Role(name="user", description="Default user role", is_default=True))
```

When no `is_default` row exists, the callback logs a warning and the
user lands with empty `roles` — every `RequireResource(...)` route will
return 403 until an operator attaches a role manually. The
warning to grep for: `OAuth: no Role.is_default configured`.

## Picking a combination

```dotenv
# API-key only (default)
AUTH_ENABLED_PROVIDERS=["api_key"]

# JWT only
AUTH_ENABLED_PROVIDERS=["jwt"]
JWT_SIGNING_KEY=<32+ byte secret>

# Browser SSO + server-to-server
AUTH_ENABLED_PROVIDERS=["jwt","api_key","oauth_google"]
JWT_SIGNING_KEY=<...>
GOOGLE_OAUTH_CLIENT_ID=<...>
GOOGLE_OAUTH_CLIENT_SECRET=<...>
GOOGLE_OAUTH_REDIRECT_URI=https://example.com/api/v1/auth/google/callback
```

Order matters — the first match wins, so put the cheapest /
most-specific check first.

## Keys + rotation

- `jwt_signing_key` is `SecretStr` so it never lands in `repr()` or
  log dumps. Store in AWS Secrets Manager in production; the
  `CoreSettings` AWS source picks it up automatically.
- HS256 + symmetric keys: rotate by issuing the new key alongside
  the old; verify both for the refresh-TTL window, then drop the old
  key. RS256 is recommended for multi-region deployments where the
  public key can be distributed without re-deploying the verifier.
- The refresh-token blacklist lives in the resilience cache
  (`jwt_blacklist_cache_alias`). Redis-backed cache makes logout
  survive worker restarts; the in-memory fallback degrades the
  guarantee to per-worker only.

## Exception family

| Exception | HTTP | error_code |
|---|---|---|
| `AuthenticationFailedError` | 401 | `AUTHENTICATION_FAILED` |
| `APIKeyRevokedError` | 401 | `API_KEY_REVOKED` |
| `TokenExpiredError` | 401 | `TOKEN_EXPIRED` |
| `TokenInvalidError` | 401 | `TOKEN_INVALID` |
| `TokenRevokedError` | 401 | `TOKEN_REVOKED` |
| `PermissionDeniedError` | 403 | `PERMISSION_DENIED` |

Mappings are registered centrally in
`src/core/exceptions/handlers.py`; new families register themselves
the same way.

## RBAC

`src.core.rbac.RequireResource(resource, action)` runs after
authentication and consults the `(resource, action)` permissions on
the authenticated user's roles. A superuser role bypasses the check
entirely. See `src/core/rbac/CLAUDE.md`.
