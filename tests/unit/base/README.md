# tests/unit/base

Unit tests for the reusable base classes under `src/core/base/`.

**Allowed to touch**: `BaseSchema`, `EncryptedString`, base-class
constructor / class-method behaviour that has no DB dependency. The
DB-bound parts of `BaseRepository` / `BaseService` belong in
`tests/integration/repository/`.

**Production code**: `src/core/base/`.

**How to add a test**

1. Pick the class method or descriptor under test.
2. Construct the smallest fake subclass that exercises it (e.g. a
   one-column `BaseModel` declared inside the test).
3. Pin the property (round-trip, validation rejection, default value)
   without touching a SQLAlchemy session.
