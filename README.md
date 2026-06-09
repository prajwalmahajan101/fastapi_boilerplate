# FastAPI Boilerplate

[![test](https://github.com/prajwalmahajan101/fastapi_boilerplate/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/prajwalmahajan101/fastapi_boilerplate/actions/workflows/test.yml)

A batteries-included starting point for FastAPI services. It ships the
cross-cutting infrastructure you'd otherwise rebuild every time:

- **Response envelope** — every response (success or error) uses one stable
  shape (`success` / `message` / `data` / `errors` / `request_id`).
- **Typed exceptions** → HTTP-status registry (extend it from your own code).
- **Resilience layer** — circuit breaker, retry, cache, and rate-limit, each
  Redis-backed with an automatic in-memory fallback.
- **Structured logging** with request-id propagation + log sanitisation.
- **API audit log** — fire-and-forget request/response capture (Postgres/Noop).
- **Async SQLAlchemy** base model / repository / service classes.
- **Security middleware** — CSP, HSTS, body-size cap, selective CORS — plus
  AWS Secrets Manager settings and S3 / SES / SSRF-safe HTTP helpers.
- **Health probes** (`/healthz`, `/readyz`) wired to DB + resilience backends.

The `Item` resource and `/api/v1/hello` route are **examples** that
demonstrate the wiring. Delete them once your own routes land.

## Layout

```
src/api/        HTTP routes (health, versioned API)
src/common/     app settings, enums, OpenAPI metadata
src/core/       reusable cross-cutting infrastructure (the engine)
src/db/         engine lifecycle + model registry
src/model/      SQLAlchemy ORM models
src/repository/ async data access
src/schema/     Pydantic request/response DTOs
src/service/    business logic
src/management/ operator CLIs
src/app.py      FastAPI factory + lifespan
main.py         Uvicorn entry point
```

See [`CLAUDE.md`](CLAUDE.md) for the repo-wide rules and [`docs/`](docs/) for
the design narrative.

## Quick start (Docker)

```bash
cp .env.example .env        # fill in DB / Redis / secrets
docker compose up -d        # Postgres + Redis + migrations + app
curl localhost:8000/healthz
curl localhost:8000/api/v1/hello?name=you
```

`docker compose` runs `alembic upgrade head` (the `db-init` service) before
starting the app.

## Quick start (local, no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env        # point DB_HOST/REDIS at localhost
alembic upgrade head
python main.py              # http://localhost:8000
pre-commit install          # ruff + pydocstyle + darglint + pip-compile
```

## Build your own resource

1. **Model** — add a class under `src/model/` (extend `BaseModel` /
   `NamedBaseModel`); re-export it from `src/model/__init__.py`.
2. **Migration** — `alembic revision --autogenerate -m "add <table>"`,
   review, then `alembic upgrade head`. Update [`docs/erd.md`](docs/erd.md).
3. **Schema** — add `*Create` / `*Update` / `*Read` DTOs under `src/schema/`.
4. **Repository** — extend `BaseRepository[Model]` under `src/repository/`.
5. **Service** — extend `BaseService` / `BaseNamedModelService` under
   `src/service/`; put domain rules in the pre/post hooks.
6. **Route** — add a router under `src/api/v1/`, mount it in
   `src/api/v1/__init__.py`, wrap writes in `async with atomic(session):`,
   and return `SuccessResponse` / `PaginatedResponse`.

The `Item` example walks through all six layers — copy its shape.

## Configuration

All settings load via `src.common.settings.Settings` (a subclass of
`CoreSettings`). Priority: **AWS Secrets Manager → environment → `.env` →
defaults**. See [`.env.example`](.env.example) for the available knobs.

## Tooling

- `ruff` (lint + format), `pydocstyle` + `darglint` (docstrings), `mypy`
  (types) — all wired into `.pre-commit-config.yaml`.
- `scripts/check_dead_utils.py` flags unused public symbols under `src/core/`.
