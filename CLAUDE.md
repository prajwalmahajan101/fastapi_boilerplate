# CLAUDE.md — repo-wide operating rules

> Start here. [`README.md`](README.md) is for setup; [`docs/`](docs/) is for
> design narrative; per-module `CLAUDE.md` files are for module-specific
> conventions. **This file is for the rules that apply repo-wide.**

## What this is

A batteries-included **FastAPI service boilerplate**. It ships the
cross-cutting infrastructure most services re-implement from scratch:

- a standard response **envelope** (`success` / `message` / `data` /
  `errors` / `request_id`) and a typed exception → HTTP-status registry;
- a **resilience** layer — circuit breaker, retry, cache, and rate-limit —
  each backed by Redis with an automatic in-memory fallback;
- **structured logging** with request-id propagation and log sanitisation;
- a fire-and-forget **API audit log** (`api_log`) with a Postgres/Noop backend;
- async **SQLAlchemy** base model / repository / service classes;
- security middleware (CSP, HSTS, body-size cap, selective CORS), AWS
  Secrets Manager settings, and S3 / SES / SSRF-safe HTTP helpers.

The example `Item` resource (`model` → `repository` → `schema` → `service`
→ `api/v1/items.py`) and the `/api/v1/hello` route exist only to show the
wiring. **Delete them** once your own routes land.

## Repository layout

```
alembic/      migrations          docs/         narrative docs (mermaid)
requirements/ pinned deps         scripts/      operator utilities
src/api/      HTTP routes         src/common/   app settings, enums, OpenAPI metadata
src/core/     cross-cutting infra src/db/       engine lifecycle (no models)
src/management CLIs               src/model/    SQLAlchemy ORM
src/repository data access        src/schema/   pydantic schemas
src/service/  business logic
main.py       Uvicorn entry      src/app.py    FastAPI factory + lifespan
```

Each `src/<module>/` has its own `CLAUDE.md`. Read it before touching the
module.

## Documentation: thin now, exact later

> The per-module `CLAUDE.md` files and the `docs/` files shipped with this
> boilerplate are **deliberately thin and generic** — placeholders that
> describe the *shape* of each layer, not a specific product.
>
> **Flesh them out as the project grows.** When you add a real domain
> concept, a new table, a new partner integration, or an auth flow, write
> the exact convention into that module's `CLAUDE.md` and the matching
> `docs/` file *in the same change*. Treat the generic text as a template
> to replace, not boilerplate to preserve.

## Development workflow

- Boot stack: `docker compose up -d` (Postgres + Redis + app).
- Migrations: `alembic revision --autogenerate -m "<msg>"` then
  `alembic upgrade head`.
- Run server (no Docker): `python main.py`.
- Run tests: `pytest` (config in `pytest.ini`; the suite needs no Postgres
  or Redis — see `tests/conftest.py`).
- Pre-commit hooks: `pre-commit install`.

## Git workflow rules

These are non-negotiable.

### Atomic commits

- **One logical change per commit.** Group into clean buckets: feature,
  refactor, bug fix, perf, tests, docs, style, chore. Don't mix buckets.
- **Order commits correctly.** Infrastructure before features that depend
  on it. Models before views. Schema before route.
- **Stage by specific paths.** Never `git add .` or `git add -A` — both
  pick up secrets and stray edits. Stage explicit paths. If one file mixes
  concerns, split with `git add -p`.

### Commit message format

```
type: short summary in imperative present tense

Body — what changed and why. Wrap at 72 chars. Explain motivation, not
the diff.
```

- **Header ≤72 chars**, imperative present tense ("add", not "added").
- **Body explains what + why.** The diff shows how.

### Allowed types

| Type | Use for |
|---|---|
| `feat` | New user-visible capability or behavior. |
| `bugfix` | Defect fix. (This repo uses `bugfix`, not `fix`.) |
| `refactor` | Restructure without behavior change. |
| `perf` | Performance improvement. |
| `chore` | Tooling, deps, config, migration revision IDs. |
| `build` | Build / packaging / container config. |
| `docs` | Documentation only. |
| `test` | Tests only. |
| `style` | Formatting / whitespace / linting (no logic change). |

### Hard rules

- **Never `--no-verify`.** If a hook fails, fix the root cause in a new commit.
- **Never amend a published commit.** Commit on top instead.
- **No empty commits.** If nothing is staged, stop.
- **Don't commit secrets.** `.env` is gitignored. If a `.env` is staged, abort.

## Branch-naming rules

Trunk is `main`. Topic part is `snake_case`; no initials, dates, or ticket
IDs (put those in the body / PR title).

| Prefix | Use for |
|---|---|
| `feature/` | New capability. |
| `bugfix/` | Defect fix. |
| `hotfix/` | Production hotfix. |
| `refactor/` | Restructure, no behavior change. |
| `perf/` | Performance work. |
| `chore/` | Tooling / deps / config. |
| `docs/` | Documentation-only branch. |
| `test/` | Tests-only branch. |

## Docstring rule

> **Every code change MUST update affected docstrings in the same commit.**

- Style: PEP 257 + Google-style `Args:` / `Returns:` / `Raises:` sections.
  `pydocstyle` + `darglint` enforce this via pre-commit on `src/**.py`.
- Route handlers: keep the docstring in sync with the `summary` /
  `description` in `src/common/openapi_metadata.py`.
- Module docstrings explain "what is this module and why does it exist".

## Documentation rule

> **Any change touching architecture, routes, ORM models, exception
> families, security headers, or rate limits MUST update the corresponding
> file under `docs/` in the same commit.**

| Change type | Docs to update |
|---|---|
| Layered structure, request lifecycle, resilience layer | [`docs/architecture.md`](docs/architecture.md) |
| New / changed table, column, constraint, index | [`docs/erd.md`](docs/erd.md) + Alembic revision |
| New base class, repository/service surface, exception family | [`docs/class-diagrams.md`](docs/class-diagrams.md) |
| Security headers, CSP, CORS, rate limits, audit log | [`docs/security.md`](docs/security.md) |

A code change without the matching doc change is incomplete.

## OpenAPI metadata rule

Route changes must keep these in sync, in the same commit:

1. The route decorator (`@router.post(..., responses=..., dependencies=...)`).
2. The route docstring.
3. `src/common/openapi_metadata.py` — `TAGS_METADATA`, `DEFAULT_RESPONSES`,
   and the per-status `RESPONSES_*` dicts.

## Per-module CLAUDE.md

When you touch a module, read its `CLAUDE.md` first:

- [`src/api/CLAUDE.md`](src/api/CLAUDE.md) — HTTP routes
- [`src/common/CLAUDE.md`](src/common/CLAUDE.md) — settings, enums, OpenAPI metadata
- [`src/core/CLAUDE.md`](src/core/CLAUDE.md) — cross-cutting infrastructure
- [`src/db/CLAUDE.md`](src/db/CLAUDE.md) — engine lifecycle
- [`src/model/CLAUDE.md`](src/model/CLAUDE.md) — SQLAlchemy ORM
- [`src/repository/CLAUDE.md`](src/repository/CLAUDE.md) — async data access
- [`src/schema/CLAUDE.md`](src/schema/CLAUDE.md) — pydantic schemas
- [`src/service/CLAUDE.md`](src/service/CLAUDE.md) — business logic
- [`src/management/CLAUDE.md`](src/management/CLAUDE.md) — operator CLIs

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **fastapi_boilerplate** (2097 symbols, 4041 relationships, 162 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/fastapi_boilerplate/context` | Codebase overview, check index freshness |
| `gitnexus://repo/fastapi_boilerplate/clusters` | All functional areas |
| `gitnexus://repo/fastapi_boilerplate/processes` | All execution flows |
| `gitnexus://repo/fastapi_boilerplate/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
