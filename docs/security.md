# Security

> Thin starter doc. The boilerplate ships **no authentication** — add your
> own (API key, OAuth, JWT, …) and document the flow here. Everything below
> describes the protections that *are* wired in.

## Response security headers

`SecurityHeadersMiddleware` (toggle: `SECURITY_HEADERS_ENABLED`, default on)
attaches to every response:

- `Strict-Transport-Security` (HSTS)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy`, `Permissions-Policy`
- a strict `Content-Security-Policy` (relaxed only on the `/docs` path so
  Swagger UI's bundle loads — inert when docs are disabled)

## Request body cap

`ContentLengthLimitMiddleware` rejects any request whose body exceeds
`MAX_REQUEST_BODY_BYTES` (default 1 MiB) with HTTP 413, before the body is
read into the audit log. Raise it for upload endpoints.

## CORS

Off by default (`CORS_ENABLED=false`) — appropriate for a server-to-server
API. When enabled, origins/methods/headers must be listed explicitly; the
defaults are empty so a misconfigured deploy never exposes `*`. Implemented
by `SelectiveCORSMiddleware`, which can exclude path prefixes.

## Rate limiting

`rate_limit(scope, rate)` dependencies gate routes. Scopes: `endpoint`,
`burst`, `ip`, `global`, `user_tier`. Backed by Redis with an in-memory
fallback; `RateLimitHeadersMiddleware` emits `X-RateLimit-*` and a 429 +
`Retry-After` when a bucket is exhausted. Principal resolution reads
`request.state.system_id` / `api_key_id` if an auth layer sets them,
otherwise falls back to client IP.

## SSRF protection

`core.utils.ssrf.assert_public_url` (and the HTTP client's `check_ssrf`
flag) block requests to private / link-local / loopback addresses. Toggle
with `SSRF_BLOCK_PRIVATE_IPS` (default on).

## Encryption at rest

`EncryptedString` columns transparently Fernet-encrypt values using
`FIELD_ENCRYPTION_KEY`. A decryption failure raises `DecryptionError` rather
than returning garbage.

## Audit log + log sanitisation

The `api_log` subsystem captures inbound/outbound request metadata
fire-and-forget. Sensitive headers (`Authorization`, `X-API-Key`, `Cookie`,
…) are redacted before persistence, and `core.utils.log_sanitization`
scrubs secret-looking keys (`password`, `token`, `secret`, `api_key`, …)
from structured logs.

## Secrets

Settings load from AWS Secrets Manager (when `AWS_SECRET_NAME` is set) ahead
of env vars and `.env`. `.env` is gitignored — never commit real secrets.
