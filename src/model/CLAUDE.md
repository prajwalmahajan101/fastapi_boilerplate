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
