# Thread / async safety

The app runs on `asyncio`. Most modules are pure async and need no
locking; a few patterns warrant explicit notes.

## The async-singleton pattern

Every resilience provider uses the same shape:

```python
_instance: T | None = None
_lock = asyncio.Lock()

async def get_instance() -> T:
    if _instance is not None:
        return _instance
    async with _lock:
        if _instance is not None:
            return _instance
        _instance = await _build()
        return _instance
```

Why both the fast path *and* the locked re-check? Multiple coroutines
may arrive before `_instance` is set; the lock ensures only one
builds, and the re-check inside the lock prevents the second one
from overwriting the first's result.

Replicated by:

- `core/resilience/cache/provider.py`
- `core/resilience/circuit_breaker/provider.py`
- `core/resilience/throttle/provider.py`
- `auth/registry.py` (one-shot warning set guarded by import order)

The auth registry itself uses a plain dict because all registration
happens at module-import time (single-threaded), and reads are
non-mutating.

## Sync code reached from async

A handful of helpers wrap synchronous libraries:

- `pybreaker` — its internal counters use a `threading.RLock`. We
  call into them from the asyncio loop without offloading; the
  operations are microsecond-scale state mutations.
- `boto3` — AWS Secrets Manager pull happens once at boot
  (synchronous, blocking). Acceptable cost; bound the read with a
  timeout via the boto3 config if your environment is sensitive.

## Request state vs contextvars

- Per-request mutable state lives on `request.state` (Starlette).
  Modules write `request.state.auth`, `request.state.request_id`,
  `request.state.api_key` and similar.
- The logger needs the request id from any callsite, including
  background tasks. That goes through a `contextvars.ContextVar`
  bound by `RequestIDMiddleware`. **Do not** rely on
  `request.state.request_id` outside the request handler.

## Background tasks

`fire_and_forget(coro)` schedules a coroutine via
`asyncio.create_task`. The task inherits the current contextvars at
schedule time — if you need the bound `request_id`, schedule from
inside the request handler, not from a worker startup hook.

For long-running background work, use Celery
([`celery-topology.md`](celery-topology.md)). Celery tasks run in a
separate process; they need their own session (`get_async_session()`)
and their own contextvar binding.

## Shared state in multi-worker prod

uvicorn / gunicorn forks one process per worker. Every async
singleton in this codebase is **per-worker**. To share state across
workers:

- Cache + throttle + breaker → use the Redis tier.
- JWT refresh-token blacklist → backed by the cache, so already
  shared when Redis is the backend.
- Pybreaker tier is per-worker by design; pick Redis if cross-worker
  trip awareness matters.
