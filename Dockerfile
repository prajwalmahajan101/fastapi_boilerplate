# syntax=docker/dockerfile:1.6

# ── Builder ───────────────────────────────────────────────────────────
# Compiles wheels for the production deps (requirements/base.lock.txt)
# with the full C toolchain. The hash-pinned lock file guarantees the
# wheels we ship match what pip-compile produced from base.in.
# Nothing from this stage ships to runtime — only the precompiled
# wheels move forward.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements/base.lock.txt ./requirements/base.lock.txt
RUN pip wheel --wheel-dir /wheels --require-hashes -r requirements/base.lock.txt


# ── Runtime ───────────────────────────────────────────────────────────
# Minimal image: only libpq5 (asyncpg's runtime dep) plus the wheels
# from the builder. No build tools, no dev deps, no curl — the
# docker-compose healthcheck uses Python's urllib.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*.whl \
 && rm -rf /wheels

COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY main.py ./main.py

EXPOSE 8000

CMD ["python", "main.py"]
