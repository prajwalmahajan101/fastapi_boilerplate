# tests/integration/utils

Integration tests for `src/core/utils/` helpers that genuinely require
an external service.

**Allowed to touch**: the real `redis.asyncio` client (via
`redis_client`) or a real Postgres engine (via `pg_engine`). Most
helpers in `src/core/utils/` are pure and belong under
`tests/unit/utils/` — only put a helper here when its contract is
"speaks to a real backing store".

**Production code**: `src/core/utils/redis.py`, `http_client.py`,
`s3.py`, `ses.py`, `aws.py`. (S3/SES go via moto stubs in unit tests;
the integration tier is for the redis helper.)

**How to add a test**

1. Take the relevant fixture (`redis_client` or `pg_engine`).
2. Exercise the helper end-to-end.
3. Assert on the observable side effect in the store.
