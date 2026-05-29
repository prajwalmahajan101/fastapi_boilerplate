# tests/e2e

End-to-end tests that drive the full FastAPI app through
`TestClient`, with the application lifespan engaged when needed.

**Allowed to touch**: every layer — middleware, routes, services,
repositories, the audit pipeline, real Postgres, real Redis. **Pin
cross-layer behaviour**: status codes, envelope shape, request-id
propagation, audit-row creation, rate-limit headers.

**Production code**: the whole `src/` tree, via HTTP.

**How to add a test**

1. Use the `client` fixture from the root conftest for fast
   smoke-shaped tests (lifespan disabled, in-memory fallbacks).
2. Use the `live_client` fixture from this directory's conftest when
   you need the real startup wiring (Postgres-backed audit log,
   Redis-backed throttle).
3. Make the request, assert on status + body + headers, and — when
   relevant — assert on what landed in the audit table.
4. See [`test_hello_smoke.py`](test_hello_smoke.py) for the canonical
   smoke shape.
