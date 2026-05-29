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

## Common pitfalls

- **Opening transactions inside a service** — the route is the unit-of-
  work boundary. Multi-step writes inside the same `atomic()` block run
  together; the service exposes the steps as individual methods.
- **Catching `BaseCustomError` to translate it** — don't. The central
  exception handler maps every registered family to its HTTP status
  ([ADR-0002](../../docs/decisions/0002-exception-http-registry.md)).
  Let it propagate.
- **Whitelisting `allowed_filter_fields` ad-hoc** — declare it as a
  class attribute on the service so the listing surface is auditable in
  one place. Forgetting it means *every* column becomes filterable.
- **Calling other services directly across aggregate roots** — favour
  shared repositories or domain events. Direct cross-service calls
  hide ownership boundaries and make refactors painful.

## Reference example

`src/service/item_service.py` — `BaseNamedModelService[Item]` with a
`pre_create` hook normalising the business code. Use the same shape
for new aggregates.

## Tests

Reference test directory: [`tests/integration/repository/`](../../tests/integration/repository/)
— services are exercised through their repository against Postgres;
HTTP-level wiring is covered by [`tests/e2e/`](../../tests/e2e/).
See [`tests/CLAUDE.md`](../../tests/CLAUDE.md) for the tier conventions.
