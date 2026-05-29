# Resilience

Four cross-cutting tiers live under `src/core/resilience/`. Each
exposes the same shape: an `async` base contract, a Redis-backed
production impl, an in-memory fallback, and a registry-style
provider that lazy-initialises the first time it is called.

```
src/core/resilience/
  cache/            # get / set / add / incr — backs throttle + JWT blacklist
  circuit_breaker/  # OPEN / HALF_OPEN / CLOSED — wrap upstream calls
  throttle/         # token-bucket + fixed-window — gates routes
  retry/            # exponential back-off — wraps idempotent ops
  recovery.py       # background monitor — re-probes degraded Redis aliases
  registry.py       # health snapshot for /readyz
```

## Cache

`src.core.resilience.cache.provider.get_cache(alias)` returns the
async cache for `alias`. The Redis impl falls back to an in-memory
backend on connection failure; the fallback is cached so subsequent
calls do not stampede Redis. The recovery monitor escapes the
fallback once Redis recovers.

Reused by: the API-key debounce, the JWT refresh-token blacklist,
and any application code that wants a small TTL cache.

## Circuit breaker

`circuit_breaker_backend` selects the tier:

| Value | Tier | Notes |
|---|---|---|
| `auto` (default) | Redis → memory fallback | The historical behaviour. |
| `redis` | Redis → memory fallback | Same as `auto`. |
| `memory` | Async in-memory | Per-process, no Redis call. |
| `pybreaker` | `pybreaker` library | Per-process, well-tuned state machine. |

Per-breaker config — `failure_threshold`, `success_threshold`,
`recovery_timeout`, `excluded_exceptions` — composes onto the
defaults declared in `CoreSettings.resilience_defaults`.

Call wrapping:

```python
breaker = await registry.get_or_create("bhn_api")
result = await breaker.call(bhn_client.get_balance, account_id)
```

`call()` opens the breaker only on `ExternalServiceError`; business
4xx errors do **not** count as transport failures.

## Throttle

`src.core.resilience.throttle.rate_limit(scope, rate)` is a FastAPI
dependency. Scopes:

| Scope | Key |
|---|---|
| `ip` | Client IP (trusted via `X-Forwarded-For` when `trust_proxy_headers` is on). |
| `endpoint` | The route path. |
| `user_tier` | User-attached tier (defined on the user model). |
| `global` | A single shared bucket — uses Lua on Redis. |
| `burst` | Token bucket with explicit refill. |

Rates use the `"<n>/<unit>"` syntax: `"60/min"`, `"10/sec"`, `"1000/hour"`.

## Retry

`@retry(max_attempts=3, base_delay=1.0, exponential_base=2.0)`
wraps an async callable. Defaults from
`CoreSettings.resilience_defaults["retry"]`. Pair with the circuit
breaker for upstream calls; the breaker decides when to give up.

## Recovery monitor

`src.core.resilience.recovery.monitor` is a singleton background
task launched in the lifespan. It owns the list of aliases that
degraded at boot and polls them until they recover; once Redis is
back, it resets the cached provider so the next call rebuilds the
Redis-backed registry.

## `/readyz`

`src.core.resilience.registry` collects `is_healthy()` from every
provider. The readiness probe returns 503 (still serving) when any
provider reports degraded — load balancers can drain the pod and let
the recovery monitor finish its work.
