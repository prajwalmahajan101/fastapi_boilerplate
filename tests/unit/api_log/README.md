# tests/unit/api_log

Unit tests for the audit-log helpers under `src/core/api_log/`.

**Allowed to touch**: pure helper functions, dataclasses, sanitisation
logic. No Postgres, no Redis, no FastAPI app, no real `dispatch`
queue.

**Production code**: `src/core/api_log/sanitizers.py`,
`error_messages.py`, `dispatch.py` (helpers only — the actual queue
machinery is covered by an integration test).

**How to add a test**

1. Pick the helper you're exercising (e.g. `sanitize_headers`).
2. Copy the shape of `test_sanitizers.py` — import the helper,
   call it with a small input, assert on the return value.
3. Name the test after the *property* being pinned, not the function
   (`test_authorization_header_is_redacted`, not `test_sanitize_headers_1`).
