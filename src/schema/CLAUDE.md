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
