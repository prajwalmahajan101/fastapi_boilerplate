# Architecture

> Thin starter doc — update it as your service's structure solidifies.

## Layers

The codebase is a conventional layered FastAPI service. Dependencies point
**downward only**; `src.core` never imports `src.common` or any domain
package, which keeps it liftable into the next project unchanged.

```mermaid
flowchart TD
    R["src/api — routes\n(thin: validate, call service, envelope)"]
    SC["src/schema — Pydantic DTOs"]
    S["src/service — business logic + hooks"]
    RE["src/repository — SQL / data access"]
    M["src/model — SQLAlchemy ORM"]
    CO["src/core — reusable infra\n(envelope, resilience, middleware,\nexceptions, api_log, base classes)"]
    CM["src/common — settings, enums, OpenAPI metadata"]

    R --> SC
    R --> S
    S --> RE
    RE --> M
    R --> CO
    S --> CO
    RE --> CO
    CM --> CO
    R --> CM
```

## Request lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as Middleware stack
    participant H as Route handler
    participant SVC as Service
    participant DB as Postgres

    C->>MW: HTTP request
    Note over MW: body-size cap → CORS → security headers<br/>→ request-id → request/exception logging<br/>→ rate-limit headers
    MW->>H: dispatch (request_id in context)
    H->>SVC: validated DTO
    Note over H,SVC: writes run inside `async with atomic(session)`
    SVC->>DB: repository queries
    DB-->>SVC: rows
    SVC-->>H: domain objects
    H-->>MW: SuccessResponse envelope
    MW-->>C: JSON + X-Request-ID
    Note over H: @log_inbound_request queues an<br/>audit row (fire-and-forget)
```

A raised `BaseCustomError` subclass is caught by the central handler
(`core/exceptions/handlers.py`), mapped to an HTTP status via the registry,
and serialised into the same `ErrorEnvelope` shape.

## API audit log

`src.core.api_log` records one row per inbound request and per outbound
HTTP call. The capture pipeline is split into focused modules so the
hot path stays tight and the helpers stay unit-testable in isolation:

| Module | Responsibility |
|---|---|
| `inbound.py` | `@log_inbound_request` route decorator |
| `outbound.py` | `@log_outbound_request` service-method decorator |
| `dispatch.py` | `FireAndForgetQueue` + `persist_log` + the shared `capture_and_dispatch` skeleton |
| `sanitizers.py` | Pure helpers: header redaction, body truncation, JSONB-safe casts |
| `error_messages.py` | `build_error_message` (composes the audit `error_message` string) |
| `decorators.py` | Re-export shim for the historical import path |

`capture_and_dispatch` owns the shared wrapper shape (start `perf_timer`
→ `await func` → on success or failure schedule `persist_log` via the
bounded `FireAndForgetQueue`). Per-direction setup — reading the request
body synchronously for inbound, swapping the `outbound_response_meta_ctx`
ContextVar for outbound — lives in each decorator module's `wrapper`.

```mermaid
sequenceDiagram
    participant W as Wrapper (inbound)
    participant F as Route handler
    participant Q as FireAndForgetQueue
    participant R as ApiLogRepository

    W->>W: read request body bytes (sync)
    W->>F: invoke handler under perf_timer
    F-->>W: Response / raised exception
    W->>Q: submit persist_log(build_log(state))
    W-->>W: return / re-raise to caller
    Q->>R: save(ApiLog) (background task)
```

The fire-and-forget contract means a DB outage or a degraded backend
can never fail the calling request — submissions overflow the queue
with a single warning, and `persist_log` swallows repository errors
after logging them.

## Resilience layer

`src.core.resilience` provides circuit breaker, retry, cache, and
throttle/rate-limit primitives. Each backend is Redis-backed with an
**automatic in-memory fallback** if Redis is unreachable; a readiness probe
(`/readyz`) doubles as the recovery trigger. Wrap outbound calls with the
`@resilient` / `@retry_with_exponential_backoff` decorators and gate routes
with `rate_limit(...)` dependencies.

## Startup / shutdown

`src/app.py`'s lifespan: bind settings into `core.runtime` → wait briefly
for Redis → build the shared DB engine → start the api_log backend. Shutdown
reverses it, drains fire-and-forget log tasks, and disposes pools. Adding a
resource never requires touching this file.
