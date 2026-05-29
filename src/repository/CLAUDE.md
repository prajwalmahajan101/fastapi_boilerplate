# src/repository — async data access

> Thin starter notes.

- One repository per aggregate root, extending
  `src.core.base.repository.BaseRepository[Model]`.
- Repositories **own the SQL** (`select` / `update` / `delete`, loader
  options, upserts). They do **not** open transactions — the service /
  route boundary does that via `atomic`.
- Inherit the generic CRUD surface (`get_by_id`, `list`, `list_paginated`,
  `add`, `update`, `delete_hard`, …); add only the bespoke queries your
  model needs.
- Re-export each repository from `__init__.py`.

`item_repo.py` is an example.

## Common pitfalls

- **Opening a transaction inside the repository** — services / routes
  own the boundary (`async with atomic(session)`). Repositories must
  stay composable inside a larger unit of work.
- **Returning ORM models bound to a closed session** — return detached
  rows or simple values; the route handler converts to a `*Read` schema
  via `Schema.model_validate(...)`.
- **`select(Model).filter(...)`** for a hot read without `with_for_update`
  when the next step is a write — that's the classic lost-update bug.
  Use the inherited `get_for_update(...)` helper or pass `with_for_update=True`.
- **N+1 on `.list()`** — declare `loader_options=` (`selectinload` /
  `joinedload`) instead of post-hoc loading.

## Reference example

`src/repository/item_repo.py` — minimal subclass extending the generic
CRUD surface; only adds bespoke queries the example needs.
