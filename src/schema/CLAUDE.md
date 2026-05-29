# src/schema — Pydantic schemas

> Thin starter notes.

- Request/response DTOs only — the wire contract, never persistence.
- Extend `src.core.base.schema.BaseSchema` (enables `from_attributes`,
  strips whitespace, accepts by field name).
- Split by intent: `*Create` / `*Update` (inbound) vs `*Read` (outbound).
  Make `*Update` fields optional and serialise with `exclude_unset=True`.
- Never import ORM models here; routes map ORM rows to read schemas with
  `Schema.model_validate(orm_row)`.

`item.py` is an example.

## Common pitfalls

- **Importing an ORM model into a schema module** — schemas are wire
  contracts; coupling them to ORM forces every schema change through
  the DB. Map ORM → schema in the route via `model_validate`.
- **Mutable defaults (`list` / `dict` / `set` literals)** — Pydantic
  flags these, but use `Field(default_factory=...)` for any non-empty
  default so different requests don't share state.
- **`*Update` with required fields** — every `*Update` field must be
  optional; serialise with `exclude_unset=True` so partial updates
  don't overwrite columns with `None`.
- **Reusing a `*Create` for a `*Read`** — they have different surfaces
  (no `id` / `created_at` on Create, no client-only fields on Read).
  Split them; the duplication is trivial and the wire contract clarity
  is worth it.

## Reference example

`src/schema/item.py` — `ItemCreate` (writes), `ItemUpdate` (partial
writes), `ItemRead` (read shape with `from_attributes` enabled by the
`BaseSchema` parent).
