# ADR-0004: Adopt resilience-kit 0.2.0 features (PII redaction, key rotation, metrics)

- **Status:** Accepted
- **Date:** 2026-07-04
- **Deciders:** Backend infra

## Context

[ADR-0003](0003-outsource-resilience-to-resilience-kit.md) moved the
resilience subsystem onto `resilience-kit==0.1.0`. The kit cut `0.2.0`
(2026-07-04), an additive minor whose new surface maps directly onto gaps
this boilerplate had deliberately left as placeholders or, for a lending
fintech (optimoloan), onto compliance-relevant capabilities:

- The audit log only redacted **sensitive header names** — PII embedded
  inside a body value (PAN, Aadhaar, IFSC, mobile, bank account, card)
  was persisted in the clear.
- Field encryption used a single key with no rotation story.
- `src/core/metrics.py` was an explicit shim whose docstring said the
  Prometheus exporter was "a one-line change once the dependency lands".
- Outbound `AsyncAPIClient` calls carried no idempotency key.

## Decision

Pin `resilience-kit==0.2.0` (with the `[prometheus]` extra) and adopt four
of its new surfaces:

1. **Body-PII redaction** — route captured request/response bodies through
   the kit's value-scanning `RegexRedactor` (`india_fintech` pattern set)
   in `api_log`'s `serialize_body`.
2. **Key rotation** — move crypto config to the ordered
   `RESILIENCE_CRYPTO__FIELD_ENCRYPTION_KEYS` (MultiFernet) and add
   `src/management/rotate_encryption.py` to re-encrypt stored ciphertext
   onto the primary key.
3. **Prometheus metrics** — forward the `src.core.metrics` shim to the
   kit's active sink (`RESILIENCE_METRICS_SINK`) and mount an opt-in
   `GET /metrics`.
4. **Outbound idempotency** — document `auto_idempotency_key` for
   non-idempotent outbound calls and keep the kwargs out of the audit
   `extra` column.

## Consequences

### Positive

- PII embedded in bodies is masked before persistence — closes a real
  audit-log leak for a fintech.
- Zero-downtime field-key rotation becomes an operator runbook.
- Real metrics backend with no call-site changes; the shim's cardinality
  guard is retained as call-site defence.

### Negative

- The body redactor adds a regex pass over each captured body (bounded by
  `api_log_max_body_size`; toggle via `api_log_redact_body_pii`).
- One more kit extra (`[prometheus]`) in the dependency surface.

### Neutral

- The boilerplate keeps its own `src/core/utils/redis.py` pool alongside
  the kit's shared client (ADR-0017 upstream); the two are distinct
  objects and do not double-close (documented in that module).
- `get_cache`/`get_throttle`/`get_breaker` provider signatures are
  unchanged from 0.1.0; a **pre-existing** bug in
  `src/core/lifecycle/healthcheck.py` (awaiting the sync providers /
  positional `get_breaker`) is out of scope here and tracked separately.

## Alternatives considered

- **Adopt the kit's `india_fintech` sanitizer via its `AuditBackend`
  entry point** instead of the standalone redactor — rejected: this repo
  runs its own `api_log` pipeline, not the kit's `AuditBackend`; the
  redactor classes are usable standalone, so we call them directly.
- **Replace `src/core/metrics.py` entirely with the kit's free
  functions** — rejected: the local `_assert_bounded` guard fails fast at
  the call site (stricter than the kit's silent label-drop), which we
  keep as CI/test defence.
- **Adopt `[otel]` instead of `[prometheus]`** — deferred: Prometheus is
  the lower-infra default; `otel`/`sentry` remain one-env-var switches.

## References

- resilience-kit `CHANGELOG.md` `[0.2.0]`; ADRs 0012–0017.
- Dogfooding report: `resilience-kit/docs/v0.2-upgrade-reports/fastapi_boilerplate.md`.
- [`docs/key-rotation.md`](../key-rotation.md), [`docs/observability.md`](../observability.md).
