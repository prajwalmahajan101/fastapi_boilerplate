# tests/integration/api_log

Integration tests for the audit-log Postgres backend under
`src/core/api_log/`.

**Allowed to touch**: a real `AsyncSession`, the Postgres-backed audit
backend, and the `api_logs` table. **Not** the dispatch queue's pure
sequencing logic (that's a unit test).

**Production code**: `src/core/api_log/backends/`,
`src/core/api_log/repository.py`, `src/core/api_log/table.py`.

**How to add a test**

1. Take the `pg_engine` fixture and instantiate the Postgres backend.
2. Persist a sample log row.
3. Query `api_logs` directly and assert on the persisted columns
   (status code, duration, sanitised headers, redacted body).
4. Roll back on teardown.
