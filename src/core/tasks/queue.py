"""Producer-side enqueue helper.

Thin wrapper over ``celery_app.send_task`` so call sites read like:

    enqueue("send_welcome_email", user_id=42)

instead of plumbing the Celery app in by hand. The wrapper also defaults
the queue to :data:`CoreSettings.task_queue_name`, so deployments can
swap the queue name from settings without editing call sites.
"""

from __future__ import annotations

from typing import Any

from src.core.runtime import get_settings
from src.core.tasks.app import celery_app


def enqueue(
    task_name: str,
    *args: Any,
    queue: str | None = None,
    countdown: float | None = None,
    eta: Any = None,
    **kwargs: Any,
) -> Any:
    """Send a job onto the Celery queue.

    Args:
        task_name: Name of the registered Celery task (e.g.
            ``"src.tasks.email.send_welcome_email"``).
        *args: Positional arguments forwarded to the task.
        queue: Optional queue override. Defaults to
            :data:`CoreSettings.task_queue_name`.
        countdown: Seconds to wait before executing.
        eta: Absolute datetime to execute at (mutually exclusive with
            ``countdown``).
        **kwargs: Keyword arguments forwarded to the task.

    Returns:
        The Celery ``AsyncResult`` for the enqueued job.
    """
    queue_name = queue or get_settings().task_queue_name
    return celery_app.send_task(
        task_name,
        args=args,
        kwargs=kwargs,
        queue=queue_name,
        countdown=countdown,
        eta=eta,
    )


__all__ = ["enqueue"]
