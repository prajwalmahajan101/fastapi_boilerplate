# Data model

The boilerplate ships a minimal but complete auth + audit data
model. The ERD lives in [`erd.md`](erd.md); this doc adds the
narrative around it.

## Core tables

| Table | Owner | Purpose |
|---|---|---|
| `users` | auth | Authenticated principal. Email-unique. |
| `roles` | auth | Named bundle of permissions; `is_superuser_role` bypasses every check. |
| `permissions` | auth | Atomic `(resource, action)` pairs. |
| `user_roles` | auth | M2M between `users` and `roles`. |
| `role_permissions` | auth | M2M between `roles` and `permissions`. |
| `api_keys` | auth | Per-user service-to-service credentials. Secret is `EncryptedString`. |
| `api_logs` | audit | One row per inbound / outbound HTTP call. |
| `items` | example | Replace with your domain models. |

## Conventions

- Every domain table extends `BaseModel` (or `NamedBaseModel`).
  Inherited columns: `id`, `created_at`, `updated_at`, `is_active`,
  `notes` (JSONB).
- Soft-delete via `is_active=False`. Hard delete is reserved for
  GDPR-style "right to be forgotten" flows.
- DB-level enum check constraints sit alongside the application-side
  `choices=` — admin / shell / raw-SQL paths cannot smuggle in
  unknown values.
- Sensitive columns use `EncryptedString` (Fernet via
  `field_encryption_key`). The `APIKey.secret` column is the
  canonical example.

## Migration discipline

Every column / constraint / index change needs:

1. An Alembic revision (`alembic revision --autogenerate -m "..."`)
   — review the diff before committing.
2. The matching update to [`erd.md`](erd.md) in the same commit
   (enforced by the repo-wide documentation rule).
3. A check the new column has a sensible default or backfill plan
   for production rollout.

## Auth schema specifics

- `users.email` is the natural key for OAuth upsert
  (`UserRepository.get_by_email`). Match is case-sensitive; align
  with the provider's normalisation if you support more than one.
- `api_keys.prefix` has a **partial unique index** scoped to
  `is_active=True AND revoked_at IS NULL`. The auth dependency
  filters by exactly this predicate so the lookup is index-only.
- `roles.is_superuser_role` is a single-row bypass switch. Pair
  with a code-side audit when granting it.

## Reading the ERD

[`erd.md`](erd.md) is generated narrative + Mermaid. Update both
sides — the diagram and the prose — in the same commit as the
schema change.

## Tests

Repository-level round-trips live in `tests/integration/repository/`.
They run against a real Postgres instance via `docker compose up -d
postgres`. See `tests/CLAUDE.md` for the tier conventions.
