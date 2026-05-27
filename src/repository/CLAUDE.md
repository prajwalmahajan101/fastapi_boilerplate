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
