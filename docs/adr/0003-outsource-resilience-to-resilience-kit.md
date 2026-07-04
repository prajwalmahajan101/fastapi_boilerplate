# ADR-0003: Outsource the resilience subsystem to `resilience-kit`

- **Status:** Accepted
- **Date:** 2026-06-10
- **Deciders:** Backend infra

## Context

Through M7 the boilerplate shipped its own `src/core/resilience/` tree
— circuit breaker, retry, cache, throttle, the SSRF guard, the Fernet
field-encryption helper, and an async HTTP client (`AsyncAPIClient`).
The same primitives were replicated in three other internal projects
that consume the boilerplate as a starting point. Each project tracked
its own bug fixes, perf tuning, and exception shapes; keeping the four
copies in lockstep had become a known liability, surfaced repeatedly
as "X is fixed in project A but not B".

The M7 → M8 cycle extracted these primitives into a single library,
`resilience-kit`, with the explicit goal of letting downstream
projects depend on `==<pin>` instead of vendoring. By M8b the kit had
shipped `0.1.0` with the helpers the boilerplate needed to migrate
without re-implementing infrastructure (`context.bind_to`,
`adapters._envelope.from_exception`, `runtime.legacy_env_alias`,
`testing.verify_envelope_contract`,
`testing.reset_all_singletons_async`).

The vendor evaluation, blockers hit, helper inventory, and timing
breakdown live in [`docs/m8b-upgrade-report.md`](../m8b-upgrade-report.md).
This ADR ratifies the decision the report recommended.

## Decision

The boilerplate depends on `resilience-kit==0.1.0` for circuit
breaker, retry, cache, throttle, SSRF protection, Fernet field
crypto, and the async HTTP client. `src/core/resilience/`,
`src/core/utils/http_client/`, and `src/core/utils/crypto.py` were
removed in PR #6.

To keep downstream forks from rewriting every import, `src/core/__init__.py`
re-exports the kit's public surface on the historical path:
`circuit_breaker`, `resilient`, `retry_on_failure`, `rate_limit`,
`FernetCipher`, and `assert_public_url` all remain importable from
`src.core`. The dormant-code policy
([`docs/INDEX.md` § "Dormant modules"](../INDEX.md#dormant-modules))
+ the static gate in `tests/unit/scripts/test_no_dormant_imports.py`
prevent accidental re-coupling.

Two thin bridges glue the kit to the boilerplate's request lifecycle,
because the kit cannot know our envelope shape or our request-id
convention:

- **`src/core/middleware/request_id_bridge.py`** — `RequestIdBridgeMiddleware`
  publishes the kit's request-id `ContextVar` so kit-emitted exceptions
  carry the same `request_id` the boilerplate's structured logs do.
- **`src/app.py::kit_error_handler`** — translates the kit's exception
  classes through `resilience_kit.adapters._envelope.from_exception`
  into the boilerplate's `ErrorEnvelope` shape. We deliberately do
  **NOT** install the kit's bundled handlers (`install_handlers`)
  because that would emit a second, kit-native envelope alongside the
  boilerplate's — two shapes for the same wire would defeat ADR-0002.

Operator ergonomics is preserved by
`resilience_kit.runtime.legacy_env_alias()`, called at the top of
`src/core/settings.py`. Pre-M7 environment variable names
(`FIELD_ENCRYPTION_KEY`, `RATE_LIMIT_*`, `CIRCUIT_BREAKER_*`,
`REDIS_URL`, `SSRF_*`) keep working with one `DeprecationWarning` per
alias used.

## Consequences

### Positive

- **−3.5 kLOC of in-tree code.** Four resilience providers, the HTTP
  client, the SSRF guard, the Fernet helper, plus their tests, are now
  someone else's problem.
- **Single canonical implementation.** A bug fix in the kit lands in
  all four consumer projects on the next pin bump, instead of being
  cherry-picked four times.
- **Kit ships its own regression tests.** The boilerplate's test
  surface focuses on its own bridges (envelope, request-id), the
  audit pipeline, and the auth providers — not on re-validating
  primitives.
- **Envelope discipline is enforced.**
  `tests/unit/exceptions/test_envelope_contract.py` calls
  `resilience_kit.testing.verify_envelope_contract` against every kit
  exception class; if the kit adds a class that doesn't project onto
  `ErrorEnvelope`, the boilerplate's CI fails.

### Negative

- **Kit version pin tracking.** Every kit release is a coordinated
  change: read the changelog, bump `requirements/base.in`, regenerate
  the lock, run the full test suite, ratify with a CHANGELOG note. The
  M8b pin bump (`0.1.0rc1` → `0.1.0`) is the worked example
  ([PR #6](https://github.com/prajwalmahajan101/fastapi_boilerplate/pull/6),
  [`docs/m8b-upgrade-report.md`](../m8b-upgrade-report.md)).
- **Two bridges to maintain.** `request_id_bridge.py` and
  `kit_error_handler` are kit-version-coupled — a kit refactor of
  either the `ContextVar` API or `from_exception` will force a bridge
  update. Both modules are small (<60 LOC combined) and covered by
  unit tests.
- **Kit-private import path.**
  `resilience_kit.adapters._envelope.from_exception` is the documented
  public bridge despite the leading-underscore module name. We import
  the private path directly until the kit re-exports it on
  `resilience_kit.adapters`. Tracked as a kit wishlist item in
  `docs/m8b-upgrade-report.md`.
- **Doc surface churn.** Every kit-related symbol that appears in
  narrative docs had to be re-pointed at the kit. The
  `scripts/check_stale_refs.py` hook now carries patterns for the
  removed symbols (`src.core.resilience`, `core.utils.http_client`,
  `AsyncAPIClient`, `core.utils.crypto`, `recovery_monitor`) to catch
  doc-rot mechanically.

### Neutral

- **Multi-Redis topology.** The pre-M7 `redis_urls: dict[str, alias]`
  setting let each tier point at a different Redis instance. The
  kit's current single-URL surface is sufficient for the common case,
  and `legacy_env_alias` translates the single-URL legacy case. A
  multi-instance topology is on the kit wishlist; until then,
  downstream forks needing it can either pin a kit fork or wait.

## Alternatives considered

- **Keep `src/core/resilience/` embedded.** Rejected: the very thing
  that motivated the kit — four-way drift between consumer projects —
  has happened repeatedly. Continuing to embed perpetuates the same
  liability.
- **Vendor a snapshot of the kit into `src/vendor/`.** Rejected: same
  drift problem but with the rebase cost moved from "track upstream"
  to "manually merge upstream every release". The kit's release
  cadence is matched to ours; there's no scenario where vendoring
  helps.
- **Maintain a fork of the kit.** Rejected: the kit is single-org
  with shared maintainers. Forking would split the maintainer set
  across two repos and double the review surface for the same
  improvements.

## References

- Migration PR: [#6](https://github.com/prajwalmahajan101/fastapi_boilerplate/pull/6).
- Vendor evaluation + pin-bump report:
  [`docs/m8b-upgrade-report.md`](../m8b-upgrade-report.md).
- Envelope discipline (the reason kit handlers aren't installed):
  [ADR-0002](./0002-exception-http-registry.md).
- Bridges:
  [`src/core/middleware/request_id_bridge.py`](../../src/core/middleware/request_id_bridge.py),
  [`src/app.py`](../../src/app.py) (`kit_error_handler`).
- Dormant-code policy (governs the re-export shims):
  [`docs/INDEX.md` § "Dormant modules"](../INDEX.md#dormant-modules),
  [`tests/unit/scripts/test_no_dormant_imports.py`](../../tests/unit/scripts/test_no_dormant_imports.py).
- Stale-reference hook (catches doc-rot for the removed symbols):
  [`scripts/stale_refs.yaml`](../../scripts/stale_refs.yaml),
  [`scripts/check_stale_refs.py`](../../scripts/check_stale_refs.py).
- Envelope contract test:
  `tests/unit/exceptions/test_envelope_contract.py`.
