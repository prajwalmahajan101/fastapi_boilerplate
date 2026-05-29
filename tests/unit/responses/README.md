# tests/unit/responses

Unit tests for the response envelope under `src/core/responses/`.

**Allowed to touch**: the envelope factories — `SuccessResponse`,
`ErrorResponse`, `PaginatedResponse` — and the JSON body they
serialise. No FastAPI app, no middleware.

**Production code**: `src/core/responses/`.

**How to add a test**

1. Construct the factory with the smallest input that pins the
   property you care about.
2. Decode `response.body` and assert on the dict shape — the wire
   contract is what callers depend on.
3. See [`test_envelope.py`](test_envelope.py) for the canonical
   shape.
