"""Celery background tasks — broker, registry, producer surface.

Use this package whenever work must be **durable**, **retried**, or
**scheduled** — that is, whenever an in-process queue is the wrong
tool. ``src.core.utils.fire_and_forget`` remains the right tool for
best-effort fan-out (audit rows, telemetry) where a dropped task on
overflow is acceptable.

Surface
-------

* :data:`celery_app` — the configured ``celery.Celery`` instance. Pass
  this as ``-A src.core.tasks:celery_app`` to ``celery worker`` /
  ``celery beat``.
* :func:`register_task` — decorator that wraps a sync function as a
  Celery task and records it in the local registry.
* :func:`async_task` — decorator for **async** functions; each
  invocation runs the coroutine in a fresh ``asyncio.run(...)`` loop so
  the body can use the existing async repositories / cache / breaker /
  HTTP client.
* :func:`enqueue` — fire-and-track helper around
  ``celery_app.send_task`` that defaults the queue to
  :data:`CoreSettings.task_queue_name`.
"""

from __future__ import annotations

from src.core.tasks.app import celery_app
from src.core.tasks.queue import enqueue
from src.core.tasks.registry import async_task, register_task, registered_tasks

__all__ = [
    "async_task",
    "celery_app",
    "enqueue",
    "register_task",
    "registered_tasks",
]
