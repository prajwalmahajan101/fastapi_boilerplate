# tests/integration/resilience

Integration tests for `src/core/resilience/` against real Redis.

**Allowed to touch**: a real `redis.asyncio` client, the throttle /
cache / circuit-breaker / retry classes, and the keys they write.
**Not** the FastAPI app or middleware (that's e2e).

**Production code**: `src/core/resilience/`.

**How to add a test**

1. Copy [`test_throttle_redis_exemplar.py`](test_throttle_redis_exemplar.py) — the
   canonical integration shape for this area.
2. Take the `redis_client` fixture from
   `tests/integration/conftest.py` — it auto-flushes the db and
   skips when Redis isn't reachable.
3. Construct the production class against the real client, exercise
   one round-trip, assert on the key / TTL / return value.
4. Don't re-test the Lua script in isolation — that defeats the
   point of an integration test.
