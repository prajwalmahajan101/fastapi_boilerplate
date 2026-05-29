# tests/unit/middleware

Unit tests for middleware helpers under `src/core/middleware/`.

**Allowed to touch**: pure helpers — header builders, body-size
parsing, CSP/HSTS string construction. **Not** the live middleware
chain on the real app (that belongs in `tests/e2e/`).

**Production code**: `src/core/middleware/`.

**How to add a test**

1. Identify the helper (e.g. `build_csp_header(settings)`), not the
   `BaseHTTPMiddleware` subclass.
2. Pass a small fake settings object and assert on the returned
   string.
3. For end-to-end middleware behaviour (CORS, request-id
   propagation, body-cap rejection), add an e2e test instead.
