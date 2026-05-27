# src/service — business logic

> Thin starter notes. This is where your domain rules live; document them
> here as they take shape.

- One service per aggregate root, extending `BaseService[Model]` (or
  `BaseNamedModelService` for `NamedBaseModel` entities) from
  `src.core.base.service`.
- Services orchestrate repositories and enforce domain rules in the
  `pre_create` / `post_create` / `pre_update` / … hooks. They are the only
  layer routes should call.
- Services **do not** open transactions — the route wraps the unit of work
  in `async with atomic(session):`. Multiple writes in one block are atomic.
- Set `allowed_filter_fields` to whitelist `list(filters=...)` keys.
- Raise typed exceptions (`ValidationError`, `EntityNotFoundError`, …); the
  central handler maps them to HTTP status.

`item_service.py` is an example.
