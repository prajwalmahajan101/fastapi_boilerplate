"""Task decorators and the shared registry.

Two ways to define a Celery task in this boilerplate:

* :func:`register_task` — thin wrapper around ``celery_app.task`` that
  also records the task in a process-local registry for introspection.
  Use for sync tasks.
* :func:`async_task` — wraps an async function so each invocation runs
  the coroutine in a fresh ``asyncio.run(...)`` event loop on the
  Celery worker thread. Use for tasks that need the async repositories,
  cache, breaker, or HTTP client.

The registry exists mainly so tests can introspect what was wired up
without poking at Celery's private internals. The worker reads tasks
through Celery itself (autodiscover + import).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from src.core.tasks.app import celery_app

logger = logging.getLogger(__name__)


_P = ParamSpec("_P")
_T = TypeVar("_T")

# Task registry — keyed by task name (``__module__.__name__`` by
# default, overridable via ``name=`` like ``celery_app.task``).
_tasks: dict[str, Any] = {}


def register_task(
    fn: Callable[_P, _T] | None = None,
    /,
    *,
    name: str | None = None,
    **task_kwargs: Any,
) -> Any:
    """Register a sync function as a Celery task.

    Usage::

        @register_task
        def rebuild_search_index(item_id: int) -> None:
            ...

        @register_task(name="send_welcome_email", queue="email")
        def send_welcome_email(user_id: int) -> None:
            ...

    Args:
        fn: The task function (positional, when used as ``@register_task``).
        name: Optional explicit task name. Defaults to Celery's own
            ``module.name`` convention.
        **task_kwargs: Forwarded to ``celery_app.task`` (queue,
            max_retries, autoretry_for, default_retry_delay, …).

    Returns:
        A Celery ``Task`` instance.
    """

    def _decorate(func: Callable[_P, _T]) -> Any:
        task = celery_app.task(name=name, **task_kwargs)(func)
        _tasks[task.name] = task
        return task

    if fn is None:
        return _decorate
    return _decorate(fn)


def async_task(
    fn: Callable[_P, Awaitable[_T]] | None = None,
    /,
    *,
    name: str | None = None,
    **task_kwargs: Any,
) -> Any:
    """Register an **async** function as a Celery task.

    Each Celery invocation runs the wrapped coroutine in a fresh
    ``asyncio.run(...)`` loop, so the task body can use the existing
    async repositories / cache / breaker / HTTP client without leaking
    a loop across worker invocations.

    Usage::

        @async_task
        async def publish_outbox_batch(batch_id: int) -> None:
            async with atomic(get_session()) as session:
                ...

    Args:
        fn: The async task function (positional, when used as
            ``@async_task``).
        name: Optional explicit task name.
        **task_kwargs: Forwarded to ``celery_app.task``.

    Returns:
        A Celery ``Task`` instance whose ``run`` is a sync wrapper.
    """

    def _decorate(coro_fn: Callable[_P, Awaitable[_T]]) -> Any:
        @functools.wraps(coro_fn)
        def _sync_runner(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            return asyncio.run(coro_fn(*args, **kwargs))

        task = celery_app.task(name=name, **task_kwargs)(_sync_runner)
        _tasks[task.name] = task
        return task

    if fn is None:
        return _decorate
    return _decorate(fn)


def registered_tasks() -> dict[str, Any]:
    """Snapshot the registered tasks (for tests / introspection)."""
    return dict(_tasks)


def _reset_for_tests() -> None:
    """Drop every registered task entry from the local registry.

    Does NOT detach the task from the Celery app — Celery's own task
    registry is process-lifetime. Use only between test cases that
    want to assert on the local registry shape.
    """
    _tasks.clear()


__all__ = ["async_task", "register_task", "registered_tasks"]
