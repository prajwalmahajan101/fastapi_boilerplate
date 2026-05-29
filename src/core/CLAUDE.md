# src/core — cross-cutting infrastructure

> Thin starter notes. This is the reusable engine; document any non-obvious
> additions here as you extend it.

## What lives here

- `base/` — async `BaseModel` / `NamedBaseModel`, `BaseRepository`,
  `BaseService` / `BaseNamedModelService`, `BaseSchema`, `EncryptedString`.
- `exceptions/` — typed exception families + the `BaseCustomError` →
  HTTP-status registry (`register_exception_mapping`).
- `responses/` — the response envelope + `SuccessResponse` /
  `ErrorResponse` / `PaginatedResponse` factories.
- `resilience/` — circuit breaker, retry, cache, throttle/rate-limit, each
  Redis-backed with an in-memory fallback, plus a `resilience_registry`.
- `middleware/` — request-id, request/exception logging, security headers,
  body-size cap, selective CORS, rate-limit headers + `install_core_middleware`.
- `api_log/` — fire-and-forget request/response audit (Postgres/Noop
  backend). Split into `inbound` / `outbound` (decorators), `sanitizers`
  / `error_messages` (pure helpers), and `dispatch` (bounded queue);
  `decorators.py` is a re-export shim that keeps the historical import
  path stable.
- `db/` — request-scoped `get_session` dependency, the `atomic` boundary,
  and `best_effort_atomic` for log-and-swallow fan-out writes.
- `lifecycle/` — health/readiness router builders.
- `utils/` — logging, crypto, HTTP client, Redis, S3/SES, SSRF guard,
  pagination, fire-and-forget queue.
- `runtime.py` / `settings.py` — `CoreSettings` + the runtime config bridge.

## The one rule

**`src.core` must never import from `src.common` or any domain package**
(`model`, `repository`, `service`, …). It reads configuration only through
`core.runtime.get_settings()`. Keeping this direction one-way is what makes
core liftable into the next project unchanged.

`scripts/check_dead_utils.py` (pre-commit) flags any public symbol under
`src/core/` with no callers — keep the surface tight.
