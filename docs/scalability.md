# Scalability

The boilerplate is designed for horizontal scaling — fork more
workers, scale Redis + Postgres, the request path stays stateless.
A few patterns deserve explicit notes.

## Worker model

Each uvicorn worker has its own:

- Async resilience-provider singletons (cache, breaker, throttle).
- Pybreaker counters (if `circuit_breaker_backend="pybreaker"`).
- API-key debounce cache (per-worker when on the in-memory fallback).
- JWT refresh-token blacklist (per-worker on the in-memory fallback;
  shared across workers when Redis is the cache tier).

For shared state — i.e. you want a breaker that opens for *every*
worker the instant any single one sees a burst of failures — pick
the Redis-backed tiers.

## Postgres

Both the application sessions and the `api_log` Postgres backend
read `db_dsn` from the same `CoreSettings` field, so they share one
engine + pool (`db_pool_size`, `db_pool_max_overflow`).

Tune the pool against the **per-worker** request concurrency, not
the cluster total:

```
expected per-worker concurrent requests ≈ (db_pool_size + max_overflow)
```

If `pool_size` is too high relative to Postgres `max_connections` you
will see boot-time errors as workers spin up — calculate
`pool_size × workers ≤ max_connections - reserve`.

## Redis

`redis_urls` is a dict of `{alias: url}`. Aliases let you split
"hot" caches (low TTL, lots of churn) from "cold" caches (long TTL,
infrequent writes) on different Redis instances. The audit-log
pipeline and the resilience tiers can each address a different
alias.

The pybreaker tier never touches Redis; the throttle "global" scope
uses a Lua script for atomic counter ops.

### One Redis cluster, many services

When several services share a single Redis cluster, the cache layer
MUST namespace its keys per deployment — otherwise two services step
on each other's debounce / blacklist entries. Set
`cache_key_prefix` explicitly per environment:

```dotenv
# service A
CACHE_KEY_PREFIX=orders-prod
# service B
CACHE_KEY_PREFIX=billing-prod
```

The circuit-breaker and throttle tiers carry their own prefixes
(`circuit_breaker_key_prefix`, the throttle scope name) so they are
already isolated. Only the cache tier shares a flat namespace by
default.

## Audit log

The `api_log` Postgres backend buffers rows in memory and flushes
batches. Tune the trio:

- `api_log_batch_size` — rows per flush.
- `api_log_batch_max_interval_seconds` — max wait before flushing.
- `api_log_batch_queue_size` — overflow cap; newest rows dropped.

Higher batch + queue sizes amortise transaction cost but raise
peak memory. The dispatcher always honours the in-flight drain
timeout (`api_log_drain_timeout_seconds`) on shutdown.

## Celery

One queue per SLO bucket. `task_redis_alias` lets the broker live
on a different Redis instance from the cache tier; the worker
processes scale independently of the web tier.

## Hot paths

The audit pipeline is the only opinionated bottleneck. If your
workload is write-heavy (every request paired with several outbound
calls), measure the dispatcher queue depth via the `api_log` stats
hook before turning workers up further.

## "Why not WebSockets / SSE?"

The boilerplate is HTTP-first. Long-lived connections need to
coordinate with the lifespan + audit-log drain logic; until that's
designed in, prefer Celery + polling for streaming-shaped use cases.
