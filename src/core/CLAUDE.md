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
- `middleware/` — boilerplate-owned extras on top of the kit's stack:
  `MetricsMiddleware`, `RequestLoggingMiddleware`, `SelectiveCORSMiddleware`.
  Request-id, body-size cap, security headers, rate-limit headers, and
  exception logging now come from
  `resilience_kit.adapters.fastapi.install_middleware_stack`.
- `api_log/` — fire-and-forget request/response audit (Postgres/Noop
  backend). Split into `inbound` / `outbound` (decorators), `sanitizers`
  / `error_messages` (pure helpers), and `dispatch` (bounded queue);
  `decorators.py` is a re-export shim that keeps the historical import
  path stable.
- `db/` — request-scoped `get_session` dependency, the `atomic` boundary,
  and `best_effort_atomic` for log-and-swallow fan-out writes.
- `lifecycle/` — health/readiness router builders.
- `utils/` — logging, AWS, Redis, S3/SES, pagination, fire-and-forget
  queue, log sanitisation, function logger, network/timing/data helpers.
  Crypto (`FernetCipher`), the SSRF guard, and the HTTP client moved to
  `resilience_kit.{crypto,ssrf,http_client}`.
- `runtime.py` / `settings.py` — `CoreSettings` + the runtime config bridge.

## The one rule

**`src.core` must never import from `src.common` or any domain package**
(`model`, `repository`, `service`, …). It reads configuration only through
`core.runtime.get_settings()`. Keeping this direction one-way is what makes
core liftable into the next project unchanged.

`scripts/check_dead_utils.py` (pre-commit) flags any public symbol under
`src/core/` with no callers — keep the surface tight.
`scripts/check_layering.py` (pre-commit) AST-walks `src/core/` and fails
on any import of `src.common` or a domain package — the one-rule turned
into a mechanical gate.

## Common pitfalls

- **Importing from `src.common` "just for an enum"** — pull the enum
  down into `core.enums` (or pass it through `core.runtime`) instead.
  The dead-utils + layering hooks will catch you.
- **Adding a new public symbol without a caller** — `check_dead_utils.py`
  fails the commit. Either wire it in, allow-list it with a comment
  explaining why (see `best_effort_atomic` for the template), or make
  it private.
- **Lazy `from src…` import inside a closure** to "hide" the dep
  from the layering check — the AST walk in `scripts/check_layering.py`
  descends into function bodies, so a function-local forbidden import
  is caught exactly like a top-level one. If you genuinely need a lazy
  import (avoiding eager construction of an optional dep, breaking a
  real circular import), keep it lazy but make the reason explicit in
  a one-line comment.
- **A new exception family without registration** — see
  [ADR-0002](../../docs/decisions/0002-exception-http-registry.md).
  The ordering test catches you before CI does.
- **Touching the audit pipeline** — read
  [ADR-0001](../../docs/decisions/0001-fire-and-forget-audit-pipeline.md)
  first; the fire-and-forget contract is load-bearing.

## Reference examples in this repo

- Cross-cutting helper added with proper documentation:
  `src/core/utils/timing.py`.
- Optional-dep guarded import: `src/core/utils/http_payloads.py`.
- Allow-listed downstream-only helper: `src/core/db/best_effort.py`
  (see `scripts/check_dead_utils.py`'s `ALLOWLIST`).

## Tests

Reference test for the resilience layer:
[`tests/integration/resilience/test_throttle_redis_exemplar.py`](../../tests/integration/resilience/test_throttle_redis_exemplar.py).
Reference tests for the audit pipeline live under
[`tests/unit/api_log/`](../../tests/unit/api_log/). See
[`tests/CLAUDE.md`](../../tests/CLAUDE.md) for the tier conventions.
