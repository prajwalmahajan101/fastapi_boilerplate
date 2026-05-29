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

## Common pitfalls

- **Forgetting `DEFAULT_RESPONSES` in `responses=`** — the OpenAPI
  contract check (`scripts/check_openapi_metadata.py`) fails CI. Every
  route must include at least the 400 / 422 / 429 / 500 baseline.
- **Hand-building response dicts** — always go through
  `SuccessResponse(...)` / `PaginatedResponse(...)`; the envelope shape
  is the API contract and clients pattern-match on it.
- **Putting business logic in a handler** — the handler validates,
  calls a service, wraps the result. Branches and policy belong in the
  service layer.
- **Opening `async with atomic(session)` inside the service** — the
  route is the transaction boundary; multi-write blocks compose.
- **Adding a route without `@log_inbound_request(...)`** — every route
  emits one audit row. The handler must declare `request: Request` so
  the decorator can read headers / body.

## Reference examples

- Read-only listing with pagination: `src/api/v1/items.py::list_items`.
- Single-resource read with 404: `src/api/v1/items.py::get_item`.
- Minimal route shape: `src/api/v1/hello.py`.
