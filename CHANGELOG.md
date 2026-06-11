# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/).

## Unreleased

_No changes yet._

## [1.0.0] — 2026-06-11

First stable release of the FastAPI service boilerplate. Forkable as a
starting point: response envelope, typed exception → HTTP-status
registry, kit-owned resilience layer, structured logging with
request-id propagation, fire-and-forget API audit log, async
SQLAlchemy base classes, pluggable auth (API key + JWT + Google OAuth),
security middleware, and a three-tier test suite (unit / integration /
e2e) wired into CI against real Postgres + Redis service containers.

### Migrated

- **Outsourced the resilience subsystem to `resilience-kit==0.1.0`
  ([ADR-0003](docs/decisions/0003-outsource-resilience-to-resilience-kit.md)).**
  Circuit breaker, retry, cache, throttle, recovery monitor, SSRF
  guard, Fernet field crypto, and the async HTTP client now come from
  the kit. `src/core/resilience/`, `src/core/utils/http_client/`,
  `src/core/utils/crypto.py`, and `src/core/utils/ssrf.py` were
  removed. `src.core` re-exports (`circuit_breaker`, `resilient`,
  `retry_on_failure`, `rate_limit`, `FernetCipher`, `assert_public_url`)
  keep the historical import paths stable.
- Two thin in-tree bridges glue the kit to the boilerplate's lifecycle:
  `src/core/middleware/request_id_bridge.py` (`RequestIdBridgeMiddleware`,
  publishes the kit's request-id `ContextVar` so kit errors carry the
  same request-id as the boilerplate's structured logs) and
  `src/app.py::kit_error_handler` (translates kit exceptions through
  `resilience_kit.adapters._envelope.from_exception` into the
  boilerplate's `ErrorEnvelope`). The kit's bundled handlers are
  deliberately not installed — see
  [ADR-0002](docs/decisions/0002-exception-http-registry.md).
- Operator ergonomics preserved via
  `resilience_kit.runtime.legacy_env_alias()` at the top of
  `src/core/settings.py` — pre-M7 env-var names
  (`FIELD_ENCRYPTION_KEY`, `RATE_LIMIT_*`, `CIRCUIT_BREAKER_*`,
  `REDIS_URL`, `SSRF_*`) keep working with one `DeprecationWarning`
  per alias used.

### Added

- Typed success envelopes on every v1 route: `response_model=SuccessEnvelope[…]`
  on `items` (Create/List/Get/Update/Delete) and `hello`, so Swagger
  renders the concrete `data` shape instead of a free-form object.
  `scripts/check_openapi_metadata.py` enforces it at CI time.
  (ISSUE-017)
- **Dormant-module policy** — five modules ship in-tree for downstream
  forks but are not on the request path today
  (`src/core/utils/{s3,ses,function_logger}.py`,
  `src/core/api_log/outbound.py`, `src/management/run_worker.py`). They
  carry a `Dormant:` callout in their module docstring and are gated by
  `tests/unit/scripts/test_no_dormant_imports.py` — any import from
  `src/` fails the test until the import site lands with a matching
  integration test. See
  [`docs/INDEX.md` § "Dormant modules"](docs/INDEX.md#dormant-modules).
- **CI runs all three test tiers against real Postgres + Redis service
  containers** with enforced coverage floors: overall 85% (via
  `pytest.ini` `addopts = --cov-fail-under=85` and
  `pyproject.toml [tool.coverage.report] fail_under = 85`); per-package
  in CI follow-up steps — `src/core/` 90%, `src/core/api_log/` 95%,
  `src/api/`/`src/service/`/`src/repository/` 80%.
- End-to-end test coverage for the auth flows that ship enabled by
  default (`/me`, API-key create/use/revoke, RBAC 403 path, error
  envelope contract), the JWT refresh + blacklist + logout flow, and
  the OAuth Google callback (with a mocked Authlib client).
- Integration coverage for the Postgres api_log backend, the
  resilience-kit + Redis round-trip (rate-limit + fallback under Redis
  drop), the generic `BaseRepository` / `BaseService` CRUD surface,
  the selective-CORS middleware, exception handlers, the request-id
  bridge, and healthcheck branches.
- `test_no_dormant_imports.py` + `test_no_inline_auth_imports.py` —
  two new AST gates joining the existing
  `check_openapi_metadata.py` / `check_dead_utils.py` /
  `check_stale_refs.py` family.
- Apache-2.0 `LICENSE`.

### Changed

- **Breaking (internal):** `BaseRepository.list` / `list_paginated` /
  `count` and `BaseService.list` / `list_paginated` now default
  `active_only=True`. Soft-deleted rows are no longer returned unless
  callers opt in via `active_only=False`. The example `list_items`
  route drops its now-redundant explicit flag. (ISSUE-018)

### Fixed

- `capture_and_dispatch` and `persist_log` now log audit-pipeline
  failures with `extra={"service_name", "direction", "request_id",
  "log_id"}`, so a build- or save-side regression is correlatable to
  the originating call from logs alone. `capture_and_dispatch` takes
  optional `service_name=` and `direction=` kwargs for the build-fail
  correlation; inbound / outbound decorators pass them. (ISSUE-021)
- **OAuth Google role attach race** —
  `test_first_signin_attaches_default_role` flake fixed by writing the
  `user_roles` row inside the transaction that creates the user
  (`src/auth/oauth_google.py`).
- **APIKey concurrent revoke** —
  `test_concurrent_revoke_produces_one_winner` race fixed by
  `populate_existing=True` + `auth.py::get_by_id_for_update` so the
  second arrival sees the latest row state.
- **SSRF guard extraction (ISSUE-031)** and
  **Authlib error logging (ISSUE-032)** — both pre-1.0 hardening
  passes.
- **JWT blacklist `await` on synchronous `get_cache`** after the kit
  `0.1.0` upgrade — dropped the spurious await.
- **`RequireResource` session DI loss** in the auth e2e suite — fixed
  the dependency wiring so the session is the same one the route
  body uses.

### Performance

- `PostgresApiLogRepository` now batches audit writes behind an
  internal queue: a single background drain task accumulates up to
  `api_log_batch_size` rows (or up to
  `api_log_batch_max_interval_seconds` of idle) and flushes them as
  one bulk `INSERT ... ON CONFLICT DO NOTHING`. The audit subsystem no
  longer pays a per-row `engine.begin()` round-trip on the shared
  pool, so it stops competing with request-path queries under burst
  load. New settings: `api_log_batch_size` (100),
  `api_log_batch_max_interval_seconds` (1.0s),
  `api_log_batch_queue_size` (5000). (ISSUE-019)

### Refactored

- `src/core/utils/http_client.py` (643-line god-class) was split along
  seams in M7 (`_session.py`, `_auth.py`, `_errors.py`, `_client.py`)
  and then **fully extracted to `resilience_kit.http_client`** in the
  M8 kit migration. Import from the kit path going forward; the
  historical `src.core` re-exports cover the transition. (ISSUE-020)

### Coverage note

The overall 85% coverage gate is currently red at ~74%. The five
dormant modules (s3, ses, function_logger, api_log/outbound,
run_worker) contribute ~7 percentage points to the gap and are
intentionally untested until a feature wires them in. Downstream
forks that don't ship dormant code can either lower the gate in
`pytest.ini` or `omit` the dormant paths from `[tool.coverage.run]`.
Tracked as ISSUE-038; the ratchet plan is to keep the gate honest and
fill coverage in 1.x.
</content>
</invoke>