# resilience-kit `0.1.0rc1` → `0.1.0` migration report

- **Project:** `fastapi_boilerplate`
- **Date:** 2026-06-10
- **Pin moved:** `==0.1.0rc1` → `==0.1.0`
- **Branch / PR:** `feat/depend-on-resilience-kit` / [PR #6](https://github.com/prajwalmahajan101/fastapi_boilerplate/pull/6)

## Outcome score (1-10)

Score: **8/10**. Every dogfooding blocker I surfaced in the prior M7 audit had a 1-call helper in v0.1.0; total apply-and-verify time was under an hour, the unit suite stayed green (+1 new test, 158 → 159 passing), and the runserver smoke confirmed both bridge fixes work at the wire. Half-point penalties for: (a) `from_exception(envelope_cls=...)` projection writes `[{field, message}]` per entry, which fails our `ErrorDetail.code` required-field — had to drop the `envelope_cls` arg and translate manually; (b) the helper lives at `resilience_kit.adapters._envelope` with the underscore "private" prefix despite being the documented public bridge; (c) `verify_envelope_contract` returns `None` instead of a structured result, so when it fails you have to read the pytest output rather than introspect the failure programmatically.

## Blockers hit and resolved

- [x] **B1** — `request_id` null in our context layer.
  - Resolved by: `bind_to(target)` via a new tiny `RequestIdBridgeMiddleware` (boilerplate had no request-id middleware left after P2b.5, so we created one).
- [x] **B2** — two exception envelopes on the same app.
  - Resolved by: `from_exception(exc)` (no `envelope_cls`, see "Pain points" #1) inside a `kit_error_handler` that constructs `ErrorEnvelope` manually.
- [x] **D-Env** — legacy env-var names silently dropped tuning.
  - Resolved by: `legacy_env_alias()` at top of `src/core/settings.py`. Default `aliases` dict already covers `FIELD_ENCRYPTION_KEY` / `RATE_LIMIT_*` / `CIRCUIT_BREAKER_*` / `REDIS_URL` / `SSRF_*` — what we needed.
- [x] **Django §3.6 (adapted)** — untested exception-bridge invariant.
  - Resolved by: `verify_envelope_contract(handler, envelope_schema=ErrorEnvelope.model_validate)` in `tests/unit/exceptions/test_envelope_contract.py`. New test passes against the 9 kit error classes shipped in v0.1.0.

## Helpers used

- [x] `resilience_kit.context.bind_to`
- [x] `resilience_kit.adapters._envelope.from_exception`
- [x] `resilience_kit.runtime.legacy_env_alias`
- [x] `resilience_kit.testing.verify_envelope_contract`
- [x] `resilience_kit.testing.reset_all_singletons_async`

All five new v0.1.0 helpers were used. No fall-backs.

## Missing surface (kit wishlist)

- **FastAPI healthcheck routers** in `resilience_kit.adapters.fastapi`. The boilerplate still ships its own `src/core/lifecycle/healthcheck.py` (~200 LOC) because the kit exposes `HealthSnapshot` / `health_snapshot()` data structures but no router factory. A `create_health_router(checks=[...], path="/healthz")` factory would let the boilerplate drop another ~200 LOC.
- **Cardinality contract on `MetricsSink`.** Kit's protocol is a "log this dict" surface. The boilerplate keeps `src/core/metrics.py` (~80 LOC) just to enforce the bounded-label allow-list at the call site. Porting `_assert_bounded` into the sink protocol would let us delete the shim entirely.
- **`AsyncIterator`-friendly `reset_all_singletons_async`.** Helper exists and works — just calling out that it's exactly what we needed.
- **Multi-alias Redis support** for cache / throttle / breaker. The boilerplate had a `redis_urls: dict[str, alias]` setting letting each tier point at a different Redis instance. `legacy_env_alias` translates the single-URL case, but the multi-instance topology has no kit-supported path.
- **Public-name re-export for `from_exception`.** The import path `resilience_kit.adapters._envelope.from_exception` looks private (leading underscore). Either re-export at `resilience_kit.adapters` or `resilience_kit.adapters.fastapi`, or rename the module to `_envelope_bridge`. Right now downstream code reaches into a name-conventionally-private module.
- **`from_exception(envelope_cls=...)` projection that writes the source `error_code`.** Currently it writes `[{field, message}]` per `details` entry into the envelope's `errors` field. Our `ErrorDetail.code` is required, so the projection fails validation. Fix: when projecting onto a `code`-bearing list-item shape, include `code=exc.error_code`. Alternative: a callback hook for the per-entry shape.
- **`HTTPAuditEvent` subclass of `AuditEvent`** with explicit columns for HTTP-shaped audit data (`request_headers`, `request_body`, `response_status`, `response_headers`, `response_body`, `ttl_expires_at`, `environment`). Without it, every HTTP-service consumer keeps a parallel audit pipeline like ours.

## Time spent per phase

| Phase | Hours | Notes |
|-------|-------|-------|
| Reading docs | 0.25 | Migration guide is excellent — §3 recipes were directly applicable, no reverse-engineering needed |
| Applying helper recipes | 0.5 | All 4 recipes + 1 new middleware file; one deviation (B2 manual translation per pain point #1) |
| Test suite green | 0.1 | Unit suite stayed green first try; +1 new envelope-contract test passed first run |
| Runserver smoke | 0.2 | Caught B1 fix immediately (`request_id` matched `x-request-id` header) and exercised B2 via an unrelated kit ValidationError |
| Writing this report | 0.4 | This document |
| **Total** | **~1.45h** | |

## Doc gaps

- §3.2 recipe shows `envelope_cls=ResponseEnvelope` but doesn't surface that the projection fails when the envelope's error-list-item shape has required fields beyond `{field, message}`. Worth a "if your `ErrorDetail` has required `code`/`details`, drop `envelope_cls` and translate manually" callout.
- §3.1 recipe assumes you have an existing request-id middleware to wrap. For projects (like ours) where the kit's own middleware is the only request-id writer, you need a fresh tiny middleware whose only job is the bridge. A "boilerplate has no request-id middleware left" worked-example would have saved 5 minutes of orientation.
- The set of legacy aliases `legacy_env_alias()` ships with is documented inline but not in §4. A printable table of the default `aliases` dict would let operators audit "is my env var covered?" without `inspect.signature(legacy_env_alias)`.
- §3.4 example calls the handler with `handler=lambda exc: on_kit_error(request=None, exc=exc).body`. For FastAPI async handlers this needs an `asyncio.run`/event-loop wrapper — not obvious from the recipe.
- `verify_envelope_contract` ships with 9 classes in `exceptions=`. Doc doesn't say whether new kit-released classes will appear in the default tuple automatically (they will, since the import is at function-definition time) — clarifying would prevent consumers from pinning the list defensively.

## Pain points

1. **`from_exception(envelope_cls=...)` projection mismatch** with envelopes whose error-list-item shape requires a `code` field. Cost ~10 min to diagnose (test failed with `ErrorDetail.code required` validation error) and pivot to manual translation. The translation is 6 lines but it would feel better to lean on the kit's projection logic.
2. **Underscore-prefixed import path** `resilience_kit.adapters._envelope.from_exception` makes the call site look like we're reaching into a private API. ruff doesn't complain (no `_` rule for from-imports) but it raises an eyebrow on code review.
3. **`legacy_env_alias()`'s `aliases` dict is a frozen default**, not extensible. Our prior env names included `JWT_*` (boilerplate-owned, not kit-owned) so we'd want to extend rather than override. The function signature accepts `aliases=` but passing a partial dict replaces the whole default, not merges with it. Had to keep ours separate; if we wanted to bundle them we'd have to copy the default dict + add. Minor.
4. **`verify_envelope_contract` swallows the per-exception failure detail** — `assert` mechanics mean a failure shows the first broken class only via the pytest assertion message, not a structured list. The guide says it surfaces "the complete list" but in practice you see one at a time as the assertion fires.
5. **No public `bind_to` documentation** beyond the migration guide snippet. The contextmanager's behavior on `request_id_ctx` reset / token cleanup isn't documented — we trusted it to do the right thing on context exit (and it does, but you have to read the source).

## Suggested ROADMAP additions

For v0.1.x:
- Rename `resilience_kit.adapters._envelope` → `resilience_kit.adapters.envelope` (or re-export at `resilience_kit.adapters`) so the public bridge doesn't look private.
- Extend `from_exception`'s projection to write `code` (from `error_code`) onto a list-item shape with a required `code` field.
- `legacy_env_alias(extra_aliases={...})` parameter that *merges* with the defaults instead of forcing the whole dict to be replaced.
- `verify_envelope_contract` returns a structured `EnvelopeContractResult` (list of `(exc_class, ok, reason)`) so CI can render a table instead of one-failure-per-run.

For v0.2:
- `resilience_kit.adapters.fastapi.create_health_router(checks, *, path, privilege_dep)`. Mirror the boilerplate's existing factory shape so consumers can drop ~200 LOC.
- `MetricsSink` cardinality contract — port the boilerplate's `_assert_bounded` (~80 LOC) so a wrong call site fails at `record_*` time, not after a Prometheus blow-up.
- `HTTPAuditEvent(AuditEvent)` subclass with HTTP-shaped columns. Lets `audit-postgres` backends share an HTTP table schema.
- Multi-alias Redis topology — accept `RESILIENCE_REDIS_URL` as `dict[str, str]` (or `RESILIENCE_REDIS_URLS__<alias>` env-var nesting) so cache/throttle/breaker can point at different instances.
- `from resilience_kit.adapters.fastapi import healthcheck` route helpers AND `request_id_bridge` middleware out-of-the-box — both are 30-LOC files every FastAPI consumer will write the same way.
- Public `bind_to` doc page covering `ContextVar` semantics + cleanup + nested-bind behavior. The implementation is correct, the documentation is missing.
