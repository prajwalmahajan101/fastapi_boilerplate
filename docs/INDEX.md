# docs/ — index

This index is the entry point to every narrative doc shipped with the
boilerplate. Per-module conventions live in `src/<module>/CLAUDE.md`;
this folder is for the cross-cutting "how + why" of the platform.

## Architecture & design

| File | Covers |
|---|---|
| [`architecture.md`](architecture.md) | Layered structure, request lifecycle, resilience layer |
| [`class-diagrams.md`](class-diagrams.md) | Base classes + exception hierarchy |
| [`data-model.md`](data-model.md) | Domain model overview; pointer to `erd.md` |
| [`erd.md`](erd.md) | Database tables (entity-relationship diagram) |

## Operations

| File | Covers |
|---|---|
| [`configuration.md`](configuration.md) | `CoreSettings` precedence, per-environment profiles |
| [`environment.md`](environment.md) | Auto-generated env-var matrix (do not hand-edit) |
| [`deployment.md`](deployment.md) | Docker / uvicorn topology, health probes, secrets |
| [`dependency-management.md`](dependency-management.md) | `requirements/*.in` → `*.txt`, pip-audit, lock policy |
| [`development.md`](development.md) | Local boot, pre-commit, common task recipes |

## Cross-cutting infrastructure

| File | Covers |
|---|---|
| [`authentication.md`](authentication.md) | Pluggable auth providers (API-key / JWT / Google OAuth) |
| [`security.md`](security.md) | Security headers, CORS, rate limits, audit log |
| [`exceptions.md`](exceptions.md) | Exception → HTTP-status registry, families |
| [`resilience.md`](resilience.md) | Cache, circuit breaker, throttle, retry, recovery monitor |
| [`observability.md`](observability.md) | Structured logging, request-id, metrics, audit log |
| [`audit-trail.md`](audit-trail.md) | `api_log` pipeline — dispatcher, sanitisers, shapes |
| [`celery-topology.md`](celery-topology.md) | Background task queue, worker, beat |
| [`thread-safety.md`](thread-safety.md) | Async-singleton patterns used across providers |
| [`scalability.md`](scalability.md) | Multi-worker considerations, Redis-shared state |

## Process

| File | Covers |
|---|---|
| [`testing.md`](testing.md) | Three-tier test layout (unit / integration / e2e) |
| [`adding-a-new-app.md`](adding-a-new-app.md) | Step-by-step for adding a new resource |
| [`decisions/`](decisions/) | Architectural Decision Records (ADRs) |

## Module references

Per-module CLAUDE.md files document module-specific conventions:

- [`../src/api/CLAUDE.md`](../src/api/CLAUDE.md) — HTTP routes
- [`../src/common/CLAUDE.md`](../src/common/CLAUDE.md) — settings, enums, OpenAPI metadata
- [`../src/core/CLAUDE.md`](../src/core/CLAUDE.md) — cross-cutting infrastructure
- [`../src/db/CLAUDE.md`](../src/db/CLAUDE.md) — engine lifecycle
- [`../src/model/CLAUDE.md`](../src/model/CLAUDE.md) — SQLAlchemy ORM
- [`../src/repository/CLAUDE.md`](../src/repository/CLAUDE.md) — async data access
- [`../src/schema/CLAUDE.md`](../src/schema/CLAUDE.md) — pydantic schemas
- [`../src/service/CLAUDE.md`](../src/service/CLAUDE.md) — business logic
- [`../src/management/CLAUDE.md`](../src/management/CLAUDE.md) — operator CLIs
- [`../tests/CLAUDE.md`](../tests/CLAUDE.md) — tests
