# tests/unit/utils

Unit tests for stand-alone helpers under `src/core/utils/`.

**Allowed to touch**: pure functions, dataclasses, context managers
that touch only stdlib. No network, no Redis, no Postgres, no
FastAPI.

**Production code**: `src/core/utils/timing.py`,
`pagination.py`, `http_payloads.py`, `log_sanitization.py`,
`ssrf.py`, and the rest of `src/core/utils/`.

**How to add a test**

1. Copy the shape of [`test_pagination.py`](test_pagination.py) — the
   canonical pure-unit exemplar.
2. Pin one observable property per test.
3. If your helper needs a clock, use `time.monotonic` + a real
   `time.sleep(0.001)` rather than a mock — these tests are already
   sub-millisecond.
