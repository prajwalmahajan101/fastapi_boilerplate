# Audit trail (`api_log`)

Every inbound HTTP request and every outbound third-party call lands
in `api_logs` as one row. The pipeline is fire-and-forget: the
request handler never waits for the audit write, so a degraded audit
backend cannot stall a request.

```
src/core/api_log/
  inbound.py     # @log_inbound_request decorator
  outbound.py    # @log_outbound_request decorator
  dispatch.py    # bounded queue + handler wrapping
  sanitizers.py  # redact secrets, cap payloads
  backends/      # postgres, noop
  factory.py     # build the configured backend
```

## Inbound

Every route hangs `@log_inbound_request(service_name="<name>")` off
its handler. The decorator extracts the method, path, headers,
body, status, and duration; sanitises the headers (`Authorization`,
`X-API-Key`, `Cookie` redacted by default — see
`api_log_sensitive_headers`); and emits the row via
`fire_and_forget`.

Body capture is toggleable: `api_log_capture_request_body` /
`api_log_capture_response_body`. Bodies above
`api_log_max_body_size` are truncated with a marker.

Captured bodies (inbound **and** outbound) are additionally scanned for
embedded PII by `sanitizers.scrub_body_pii`, which delegates to
resilience-kit's value-scanning `RegexRedactor`: a PAN / Aadhaar / IFSC /
mobile / bank-account (or email / Luhn-card) inside a body value is masked
to `[REDACTED]` before persistence — the scan runs on the full body before
truncation so a match is never half-exposed by the length cap. Toggle with
`api_log_redact_body_pii`; choose the pattern set with
`api_log_pii_pattern_set` (`india_fintech` | `global`).

## Outbound

`resilience_kit.http_client.AsyncAPIClient` (now kit-owned, see
[ADR-0003](decisions/0003-outsource-resilience-to-resilience-kit.md))
wraps the outbound HTTP path and pairs each call with a logged row
carrying the destination URL, request/response payloads, and status.

## Dispatch

`dispatch.fire_and_forget(...)` accepts a coroutine and schedules
it via `asyncio.create_task`, bounded by the backend's internal
queue (`api_log_batch_queue_size`). Overflow drops the **newest**
row with a warning — the same contract `FireAndForgetQueue` uses.

The Postgres backend buffers rows and flushes batches of
`api_log_batch_size` rows or every
`api_log_batch_max_interval_seconds`, whichever comes first.

## Shutdown

The lifespan calls `close_repository()` → drain the queue for up to
`api_log_drain_timeout_seconds`, then dispose the engine. Keep the
timeout shorter than Kubernetes's `terminationGracePeriodSeconds`
or rows will be dropped on pod termination.

## Backends

| Backend | When |
|---|---|
| `postgres` | Default — same DSN/pool as application sessions. |
| `noop` | Smoke deployments + tests that should not assert on rows. |

The `noop` backend implements the full async interface and discards
every row.

## Querying

`api_logs` is a wide table partitioned by `api_log_ttl_days` —
operators schedule a job that drops rows older than the TTL. Pin
`request_id` in queries; it stitches inbound + outbound rows of a
single request together.

## See also

- [ADR-0001](decisions/0001-fire-and-forget-audit-pipeline.md) —
  why the pipeline is fire-and-forget and what we sacrificed to get there.
- `src/core/api_log/CLAUDE.md` — module-level conventions.
