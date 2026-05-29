# src/model — SQLAlchemy ORM

> Thin starter notes. Document table-specific invariants here as models land.

- Extend `BaseModel` (id + `created_at` / `updated_at` / `is_active` +
  `notes` JSONB) or `NamedBaseModel` (adds a `name` + unique `code`) from
  `src.core.base.model`.
- One model per module; re-export it from `__init__.py` so `src.db.tables`
  registers it on `BaseModel.metadata`.
- Use `EncryptedString` (from `src.core.base`) for fields that must be
  encrypted at rest.
- After any column/constraint change: `alembic revision --autogenerate`,
  review the migration, then update [`docs/erd.md`](../../docs/erd.md) in
  the same commit.

`item.py` is an example — replace it with your domain models.

## Common pitfalls

- **Forgetting to re-export from `__init__.py`** — `src/db/tables.py`
  imports through the package surface; an un-exported model is invisible
  to Alembic autogenerate.
- **Hard-deleting instead of soft-deleting** — the `BaseModel.is_active`
  flag is the canonical soft-delete switch; the service hooks (`pre_delete`,
  `post_delete`) cascade it to children. Use `delete_hard()` only when a
  GDPR / right-to-be-forgotten request is the trigger.
- **Adding a column without updating `docs/erd.md`** — the repo-wide
  Documentation rule treats the change as incomplete. Update the diagram
  in the same commit as the migration.
- **Putting business logic in a property or hybrid method** — model
  classes are persistence shapes only. Business rules belong in
  `src/service/`.

## Reference example

`src/model/item.py` — a minimal `NamedBaseModel` subclass showing the
shape, the column conventions, and the `__tablename__` declaration.

## Tests

Reference test directory: [`tests/integration/repository/`](../../tests/integration/repository/)
— ORM models are exercised via repository round-trips against
Postgres. See [`tests/CLAUDE.md`](../../tests/CLAUDE.md) for the tier
conventions.
