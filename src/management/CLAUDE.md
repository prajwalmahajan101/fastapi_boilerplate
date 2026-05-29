# src/management — operator CLIs

> Thin starter notes.

- One module per command, each with a `main()` entry, runnable as
  `python -m src.management.<name>`.
- `init_db.py` — emergency `metadata.create_all` DDL bootstrap. **Alembic
  is the canonical schema source** (`alembic upgrade head`); use this only
  when Alembic is unavailable, then `alembic stamp head`.

Add maintenance / backfill / one-shot operational scripts here rather than
ad-hoc scripts at the repo root.

## Common pitfalls

- **Forgetting `configure(settings)`** — the CLI runs outside the
  FastAPI lifespan, so `core.runtime.get_settings()` is unbound until
  you call `core.runtime.configure(settings)` at the top of `main()`.
  Without it, every settings read inside `src.core.*` raises.
- **Leaking the engine on exit** — wrap your work in
  `try: ... finally: await engine.dispose()`. `core.utils.db.get_app_engine()`
  caches by DSN, so leaving it open keeps connections around until the
  process dies.
- **Long-running CLIs that hold a single session** — use a fresh
  session per atomic unit; bind it to the engine via
  `get_sessionmaker(engine)()`. Repository methods take a session
  argument.
- **Inventing a new logging setup** — `core.utils.logging.get_logger`
  is configured by `configure(settings)`; reuse it so CLI output goes
  through the same redaction pipeline as the server.

## Reference example

`src/management/init_db.py` — shows the `configure(settings)` →
`get_app_engine()` → `metadata.create_all(...)` → `await engine.dispose()`
shape every management CLI should mirror.
