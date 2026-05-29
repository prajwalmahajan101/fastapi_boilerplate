# Celery topology

The boilerplate ships a Celery setup with a Redis broker and a
single default queue. The wiring lives under `src/core/tasks/`.

```
src/core/tasks/
  app.py        # Celery() factory — broker/result URL, serialiser, eager toggle
  queue.py      # enqueue() helper + retry defaults
  registry.py   # task discovery (auto-loads src/service/**/*.py)
```

## Settings

| Setting | Purpose |
|---|---|
| `task_redis_alias` | Which Redis alias from `redis_urls` to use as broker. |
| `task_queue_name` | The default queue (`-Q`) name. |
| `task_max_tries` | Becomes Celery's `task_default_max_retries`. |
| `celery_result_backend` | Override — defaults to the broker URL. |

## Running the worker

```bash
celery -A src.core.tasks:celery_app worker -Q $TASK_QUEUE_NAME --loglevel INFO
```

For periodic jobs:

```bash
celery -A src.core.tasks:celery_app beat --loglevel INFO
```

## Producing a task

```python
from src.core.tasks import enqueue

enqueue("src.service.report.generate_report", report_id=42)
```

`enqueue` wraps `apply_async` with the configured retry policy and
emits an audit row via `fire_and_forget` so producer-side calls are
visible in the same `api_logs` stream.

## Task module conventions

- One Celery task per business action; thin wrappers that delegate
  to a `BaseService` method.
- Idempotent by design — the broker may re-deliver on worker crash.
- Avoid pulling the request-scoped `AsyncSession` — open a new
  session via `src.db.get_async_session()` inside the task.

## Scaling

The default queue is fine until per-task latency varies wildly
(e.g. lots of small jobs + a few minutes-long jobs). Split into
queues by SLO:

```bash
celery -A src.core.tasks:celery_app worker -Q realtime,batch \
    --concurrency 4
```

Update `task_queue_name` per producer if you want it to default to
a non-default queue.

## Shutdown

The lifespan does not own the workers; they live in their own
containers. Make sure `signal` handlers (Celery's default) are
honoured by the supervisor — `kill -TERM` is the canonical
shutdown signal.
