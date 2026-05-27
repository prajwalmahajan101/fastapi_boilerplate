# src/api/v1 — versioned API surface

> Thin starter notes. Flesh out with real resource conventions as routes land.

- One module per resource (`items.py`, …), each exposing a `router`.
- Mount every router in `__init__.py` on `v1_router` with a `prefix` and
  `tags`.
- Bump to a new package (`v2/`) for breaking contract changes; keep `v1`
  serving until clients migrate.

`hello.py` and `items.py` are reference examples — delete them once your
own routes exist.
