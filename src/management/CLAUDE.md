# src/management — operator CLIs

> Thin starter notes.

- One module per command, each with a `main()` entry, runnable as
  `python -m src.management.<name>`.
- `init_db.py` — emergency `metadata.create_all` DDL bootstrap. **Alembic
  is the canonical schema source** (`alembic upgrade head`); use this only
  when Alembic is unavailable, then `alembic stamp head`.

Add maintenance / backfill / one-shot operational scripts here rather than
ad-hoc scripts at the repo root.
