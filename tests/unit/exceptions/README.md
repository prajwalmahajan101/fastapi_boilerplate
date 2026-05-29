# tests/unit/exceptions

Unit tests for the typed exception → HTTP-status registry under
`src/core/exceptions/`.

**Allowed to touch**: the registry itself, the `BaseCustomError`
hierarchy, the handler ordering verification. No FastAPI app, no
real request.

**Production code**: `src/core/exceptions/`.

**How to add a test**

1. Register or look up a fake exception via the registry helpers.
2. Pin the resolved HTTP status / envelope shape.
3. If you add a new exception family in `src/core/exceptions/`, add a
   handler-ordering case here — see [`test_handler_ordering.py`](test_handler_ordering.py).
