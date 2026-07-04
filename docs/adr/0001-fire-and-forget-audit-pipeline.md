# ADR-0001: Use a bounded fire-and-forget queue for the API audit log

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Backend infra

## Context

Every inbound HTTP request and every outbound HTTP call needs to land
one row in `api_logs` so we can replay user-visible failures, debug
partner integrations, and pull SLO numbers off historical data. The
naive write-path (await the repository inside the handler before
returning the response) couples request latency to the audit DB's
health: any slow query or transient outage on the audit backend turns
into user-visible latency or 5xx noise on the request path itself.

The audit row is observational. It must never be the reason a
successful business operation looks like a failure to the caller.

## Decision

The audit pipeline is fire-and-forget through a bounded background
queue (`src.core.utils.fire_and_forget.FireAndForgetQueue`) drained by
`src.core.api_log.dispatch.persist_log`. The decorator's wrapper
returns / re-raises to the caller as soon as the wrapped function
returns / raises; persistence happens off the hot path.

- Capacity is bounded (`max_pending = 2000`); above it, new
  submissions are dropped with a single warning log line per overflow
  event.
- `persist_log` swallows repository errors (logs, never propagates).
- `capture_and_dispatch` swallows builder errors with the same
  contract (added 2026-05-29, ADR ratification — see
  [issue ISSUE-016 fix](../../.code_review/code_review_issues.md)).
- The FastAPI lifespan calls `drain_all(timeout=...)` on shutdown so
  in-flight tasks land before the process exits, but bounded to keep a
  degraded backend from hanging shutdown.

## Consequences

### Positive

- Audit DB outages no longer affect request latency or error rates.
- A slow audit query is visible only on the audit backend's own
  metrics, not the request-path SLOs.
- The producer-side contract is simple and uniform: submit, move on.
- The Postgres backend batches rows behind an internal queue and
  flushes ``api_log_batch_size`` rows per transaction (or whatever has
  accumulated after ``api_log_batch_max_interval_seconds``). That keeps
  audit writes off the request-path connection pool — one transaction
  per batch instead of one per row.

### Negative

- Rows can be dropped under sustained back-pressure (the bounded queue
  saturates). Surfacing this requires watching the overflow warning
  count, not the audit-row count.
- Rows can be lost on hard process termination (`kill -9`, OOM kill)
  because the drain has not run. `drain_timeout_seconds` (default 30)
  bounds the orderly shutdown wait; SIGKILL is intentionally not
  handled.
- A bug in the build_log closure used to mask the original handler
  exception — that's specifically guarded now, but the pattern is
  fragile enough that future contributors need a test (and there is
  one: `tests/core/api_log/test_dispatch.py`).

### Neutral

- Operators need to watch two metrics: queue depth (`max_pending`
  saturation) and repository save error rate.
- The Postgres backend now also has an internal batching queue
  (``api_log_batch_queue_size``). Overflow surfaces as a
  ``PostgresApiLogRepository queue full`` warning carrying
  ``service_name`` / ``direction`` / ``request_id``; treat it the
  same as the outer ``FireAndForgetQueue`` overflow.

## Alternatives considered

- **Synchronous write inside the handler.** Rejected: couples
  user-visible latency to the audit backend; a 200ms slow query turns
  the p99 of the entire endpoint into 200ms+ user-visible.
- **Unbounded background queue.** Rejected: a degraded audit backend
  would leak memory until OOM kill in a single bad incident.
- **Separate sidecar / log shipper.** Rejected for the boilerplate
  scope — adds an extra process to operate and a `JSON over UDP` (or
  similar) protocol to maintain. The in-process queue is sufficient
  for the common case; a downstream project can swap the
  `ApiLogRepository` to a sidecar writer without touching the
  decorators.

## References

- Implementation: `src/core/api_log/dispatch.py`,
  `src/core/api_log/backends/postgres.py`,
  `src/core/utils/fire_and_forget.py`.
- Regression test for the builder-failure guard:
  `tests/core/api_log/test_dispatch.py::test_capture_swallows_builder_failure_preserves_handler_exception`.
- Architecture overview: [`docs/architecture.md`](../architecture.md#api-audit-log).
