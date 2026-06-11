# Resilience

The four cross-cutting resilience tiers — **cache**, **circuit
breaker**, **throttle**, **retry** — and their supporting infrastructure
(recovery monitor, async-singleton providers, in-memory fallback) are
owned by **`resilience-kit`** (`==0.1.0`), not by the boilerplate. See
[ADR-0003](decisions/0003-outsource-resilience-to-resilience-kit.md)
for the why.

This doc covers what the boilerplate adds **on top of** the kit:

- which kit symbols are wired and how to import them;
- the two bridges (envelope + request-id) that hook kit errors into
  the boilerplate's response shape and structured logs;
- the lifespan probes that engage Redis and the recovery monitor.

For the kit's own contracts — backend selection, Lua scripts, fallback
semantics, the async-singleton pattern — read the kit's docs.

## What the boilerplate imports

Every kit symbol the request path uses is re-exported on `src.core`
so call sites stay short and downstream forks aren't coupled to the
kit's import paths:

```python
from src.core import (
    circuit_breaker,
    resilient,
    retry_on_failure,
    rate_limit,         # FastAPI dependency
    FernetCipher,       # field crypto (EncryptedString column type)
    assert_public_url,  # SSRF guard
)
```

`rate_limit` also has a public import path on the kit's FastAPI
adapter — every real call site under `src/api/v1/` uses that one:

```python
from resilience_kit.adapters.fastapi import rate_limit
```

Both paths point at the same callable; prefer the kit path in new
code for explicitness about the owning package.

## Throttle scopes

The boilerplate exercises the kit's standard scopes:

| Scope | Key |
|---|---|
| `ip` | Client IP (trusted via `X-Forwarded-For` when `trust_proxy_headers` is on). |
| `endpoint` | The route path. |
| `user_tier` | User-attached tier (defined on the user model). |
| `global` | A single shared bucket — kit uses Lua on Redis. |
| `burst` | Token bucket with explicit refill. |
| `auth` | Per-IP under the `auth:` namespace. Default 5/min, applied to `/auth/*`. See [`security.md`](security.md). |

Rates use the `"<n>/<unit>"` syntax: `"60/min"`, `"10/sec"`, `"1000/hour"`.

## The envelope bridge

The kit raises its own exception classes (`RateLimitExceeded`,
`CircuitOpenError`, validation errors, etc.) that don't know about
the boilerplate's `ErrorEnvelope` shape. `src/app.py::kit_error_handler`
catches every kit exception and translates it through
`resilience_kit.adapters._envelope.from_exception(exc)` into an
`ErrorEnvelope` with `success=False`, the populated `errors` list,
and the inbound `request_id`.

We deliberately do **NOT** install the kit's bundled handlers
(`install_handlers`). Doing so would emit a second, kit-native
envelope shape alongside the boilerplate's, breaking the single-shape
contract from [ADR-0002](decisions/0002-exception-http-registry.md).

See `src/app.py` around line 115 for the install site and the comment
explaining the deliberate omission.

## The request-id bridge

The kit publishes its own request-id `ContextVar` and reads from it
inside the resilience providers (so a throttle-blocked request carries
the same request-id in the kit's structured log lines as the
boilerplate's). The boilerplate publishes request-ids on its own
context. **`src/core/middleware/request_id_bridge.py`** —
`RequestIdBridgeMiddleware` — calls `resilience_kit.context.bind_to`
to point the kit's ContextVar at the boilerplate's value. Result:
one request-id end-to-end across both packages.

## Recovery and `/readyz`

The kit's `recovery_monitor` is a singleton background task launched
in the FastAPI lifespan (`src/app.py` `lifespan`). It owns the list
of aliases that degraded at boot and polls them until they recover;
once Redis is back, it resets the cached providers so the next call
rebuilds the Redis-backed registry.

The boilerplate's `/readyz` route (`src/core/lifecycle/healthcheck.py`)
asks the kit's registry for a health snapshot and returns 503 (still
serving) when any provider reports degraded — load balancers can
drain the pod and let the recovery monitor finish its work without
serving 5xx.

## Operator knobs

Per-tier configuration lives on `CoreSettings.resilience_defaults` and
the kit-namespaced `RESILIENCE_*` env vars. The kit's `legacy_env_alias()`
(called at the top of `src/core/settings.py`) translates pre-M7
variable names (`FIELD_ENCRYPTION_KEY`, `RATE_LIMIT_*`,
`CIRCUIT_BREAKER_*`, `REDIS_URL`, `SSRF_*`) with one `DeprecationWarning`
per alias used. See [`configuration.md`](configuration.md) for the
full env-var matrix.
