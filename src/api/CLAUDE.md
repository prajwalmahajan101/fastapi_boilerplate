# src/api — HTTP routes

> Thin starter notes. Replace the generic guidance with this project's
> concrete route conventions as they solidify.

## What lives here

- `router.py` — assembles the full URL tree: root probes (`/healthz`,
  `/readyz`) outside `/api`, everything else under the `/api` namespace.
- `health.py` — liveness/readiness routers built from `core.lifecycle`.
- `__init__.py` — mounts health + the versioned router under `/api`.
- `v1/` — the versioned API surface. One module per resource; mount it in
  `v1/__init__.py`.

## Conventions

- Handlers are **thin**: validate input (Pydantic), call a `service`, wrap
  the result in a `SuccessResponse` / `PaginatedResponse`. No business
  logic here.
- Wrap writes in `async with atomic(session):` — the transaction boundary
  is the route, not the service.
- Every response goes through the envelope factories in
  `src.core.responses`. Never hand-build a dict.
- Declare a `rate_limit(...)` dependency and the documented `responses=`
  set (`src.common.openapi_metadata`) on each route.
- Keep the docstring `summary` in sync with the OpenAPI metadata.

The `hello` and `items` routers are examples — delete them.
