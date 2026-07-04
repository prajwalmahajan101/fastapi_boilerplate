# ADR-0002: Every `BaseCustomError` must be in the exception → HTTP registry

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Backend infra

## Context

The API contract is the response envelope (`SuccessResponse` /
`ErrorEnvelope`). For errors, the envelope's HTTP status code, the
machine-readable `errors[*].code`, and the human-readable `message`
must all be consistent regardless of *which* `BaseCustomError`
subclass was raised inside the handler. If a new error subclass
escapes the handler without a registered mapping, FastAPI turns it
into a 500 with the default exception payload — which breaks the
envelope contract for any client switching on `errors[*].code`.

The registry (`src.core.exceptions.handlers.register_exception_mapping`)
is the single source of truth that connects an exception class to its
HTTP status and the envelope assembler. Registration order matters
because the central handler walks the registry in MRO order and
returns the *first* match — registering a parent before a more
specific subclass would shadow the subclass.

## Decision

Every concrete `BaseCustomError` subclass shipped (or added) under
`src.core.exceptions.*` or any downstream project must:

1. Be registered with `register_exception_mapping(ErrorClass,
   status_code)` before the app serves traffic (i.e. at module
   import time when the handlers module loads).
2. Be registered **specific-class-first**: a subclass `B(A)` is
   registered *before* `A`. The handlers module enforces this
   ordering by listing registrations bottom-up in the hierarchy.

The repo ships a regression test
(`tests/test_exception_handler_ordering.py`) that loads the registry
and asserts every concrete subclass appears before its parent in the
ordered map. CI fails when a new subclass is added without updating
the registry — the test breaks before the code ships.

## Consequences

### Positive

- The envelope contract is enforceable: a client switching on
  `errors[*].code` can never observe an undocumented code.
- Adding a new exception family is a known three-step recipe
  (subclass `BaseCustomError`, set `error_code`, register) instead of
  a hunt through the handler tree.
- The ordering test catches the most common drift mode (subclass
  added, parent forgotten) automatically.

### Negative

- One extra step at every new-exception PR (the registration).
  Mitigated by the test failing loudly when it's skipped.
- The handlers module knows about every exception family — by
  design, but it does mean cross-cutting changes touch one shared
  file.

### Neutral

- Per the root `CLAUDE.md` Documentation rule, an exception-family
  change must also update `docs/class-diagrams.md`'s exception tree.

## Alternatives considered

- **Catch-all `BaseCustomError` mapper that reads `status_code`
  off the instance.** Rejected: still requires every subclass to set
  `status_code` correctly, but the *test* gets weaker (no detection
  of a missing subclass, only a wrong code).
- **Per-route `@app.exception_handler(ErrorClass)` declarations.**
  Rejected: scatters the registry across N route modules; impossible
  to enforce ordering or completeness mechanically.

## References

- Implementation: `src/core/exceptions/handlers.py`,
  `src/core/base/exception.py`.
- Regression test: `tests/test_exception_handler_ordering.py`.
- Exception tree visualised: [`docs/class-diagrams.md`](../class-diagrams.md#exception-hierarchy).
