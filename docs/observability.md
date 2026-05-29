# Observability

Three pillars: structured logs, a per-request audit row, and metric
hooks ready to wire to Prometheus / OpenTelemetry.

## Structured logging

`src/core/utils/logging.py` configures JSON output by default
(`log_json=True`). Every record carries the bound `request_id` once
`RequestIDMiddleware` has run, so a single `grep` follows a request
across worker threads.

Toggles:

| Setting | Purpose |
|---|---|
| `log_level` | Root level — defaults to `INFO`; `LocalSettings` raises it to `DEBUG`. |
| `log_json` | JSON vs plain text. |
| `log_file_disabled` | Disable file sink — defaults `True`; cloud logs handle persistence. |
| `log_sanitize_max_string` / `log_sanitize_max_dict_keys` / `log_sanitize_max_list_items` | Caps applied by `log_sanitization.sanitize`. |
| `log_function_calls` | Toggle the `@function_logger` decorator side-effect emission. |

## Request ID propagation

`RequestIDMiddleware` (`src/core/middleware/request_id.py`) reads
`X-Request-ID` from the inbound headers or mints a UUIDv4, stores it
on `request.state.request_id`, and binds it to a contextvar so
loggers downstream pick it up. The response carries the same id back.

## Metrics middleware

`MetricsMiddleware` (`src/core/middleware/metrics_middleware.py`)
samples per-request duration into `src.core.metrics`, a shim that
ships disabled by default. Flip `metrics_middleware_enabled=True`
once you wire a Prometheus or OTel exporter to the shim hooks.

## API audit log

Every route decorated with `@log_inbound_request(service_name=...)`
emits one row via the fire-and-forget pipeline in `src/core/api_log/`.
Outbound HTTP calls through `AsyncAPIClient` are paired with
`@log_outbound_request`. See [`audit-trail.md`](audit-trail.md) for
the full pipeline.

## Recovery monitor

`src/core/resilience/recovery.py` watches every resilience provider
whose Redis alias degraded at boot, polls in the background, and
resets the cached backend once Redis comes back. Audit signal: look
for `Recovery monitor: alias 'X' recovered`.

## Health probes

- `GET /healthz` — liveness; succeeds whenever the process is up.
- `GET /readyz` — readiness; consults each resilience provider's
  `is_healthy()` and the audit backend. A 503 here means **degraded
  but serving**; load balancers can drain the pod.

### Response shape

Both probes return `{status, healthy, request_id}` to every caller.
The per-check `checks` array — which enumerates the database, cache,
throttle, and breaker backends along with their backend labels — is
**only** returned when the request is authenticated as a user with a
superuser role (`Role.is_superuser_role`). Anonymous probes (kubelet,
load balancers) get the masked body so the dependency topology is not
leaked to anyone who can hit the URL. The predicate that gates this
is `_is_superuser` in `src/api/health.py`; the masking lives in
`_envelope` (`src/core/lifecycle/healthcheck.py`).

## Quick "where do I look?"

| Symptom | Where to look |
|---|---|
| 401 / 403 spike | `log_inbound_request` audit row + `request.state.auth.provider` |
| Upstream 5xx | Circuit breaker stats via `registry.get_all_stats()` |
| Rate-limit denials | `X-RateLimit-*` headers in client logs; throttle scope stats |
| Slow request | `MetricsMiddleware` duration + audit-log `duration_ms` |
| Missing log entry | Check sanitizer caps; large payloads are truncated |
| `auth_blacklist_unreachable_*` spike | JWT blacklist cache degraded — see [`authentication.md`](authentication.md#blacklist-fail-policy). Refresh-side spikes also surface as 401s on `/auth/token/refresh`. |
