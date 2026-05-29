# tests/integration/repository

Integration tests for `src/repository/` against real Postgres.

**Allowed to touch**: an async SQLAlchemy session bound to the test
Postgres, the repository classes themselves, the ORM models. **Not**
the HTTP layer (use `tests/e2e/` for that).

**Production code**: `src/repository/`, `src/model/`,
`src/core/base/repository.py`.

**How to add a test**

1. Take the `pg_engine` fixture from `tests/integration/conftest.py`.
2. Open an `AsyncSession`, construct the repository under test,
   exercise its public methods, assert on what landed in the table.
3. Wrap the body in a transaction that you roll back on teardown so
   tests don't pollute each other.
4. If Postgres isn't running locally, the test auto-skips.
