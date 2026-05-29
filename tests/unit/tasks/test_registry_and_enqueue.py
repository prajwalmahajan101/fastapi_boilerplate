"""Unit tests for ``src.core.tasks`` — decorators + enqueue.

The Celery broker is not exercised here. Sync execution is verified
via ``task.apply(...)`` (in-process, no broker). The ``enqueue`` helper
is verified by patching ``celery_app.send_task`` and asserting on the
arguments it forwards — that path is what the integration test (which
DOES spin up Redis) covers end-to-end.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.tasks import celery_app, enqueue, registered_tasks
from src.core.tasks.registry import (
    _reset_for_tests,
    async_task,
    register_task,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with an empty task registry."""
    yield
    _reset_for_tests()


def test_register_task_records_in_registry() -> None:
    @register_task(name="tests.unit.tasks.plain_add")
    def plain_add(a: int, b: int) -> int:
        return a + b

    assert "tests.unit.tasks.plain_add" in registered_tasks()


def test_register_task_runs_via_apply() -> None:
    @register_task(name="tests.unit.tasks.plain_mul")
    def plain_mul(a: int, b: int) -> int:
        return a * b

    # ``apply`` runs the task synchronously in-process; no broker.
    result = plain_mul.apply(args=(6, 7))
    assert result.successful()
    assert result.get() == 42


def test_async_task_runs_coroutine_through_asyncio_run() -> None:
    @async_task(name="tests.unit.tasks.async_concat")
    async def async_concat(a: str, b: str) -> str:
        # Prove a real event loop ran by awaiting a no-op coroutine.
        await asyncio.sleep(0)
        return a + b

    result = async_concat.apply(args=("foo", "bar"))
    assert result.successful()
    assert result.get() == "foobar"


def test_enqueue_forwards_task_name_args_and_default_queue(monkeypatch) -> None:
    """``enqueue(name, *args, **kwargs)`` → ``celery_app.send_task(...)``."""
    fake = MagicMock(return_value="sentinel-async-result")
    monkeypatch.setattr(celery_app, "send_task", fake)

    out = enqueue("tests.unit.tasks.email", 1, 2, subject="hi")

    assert out == "sentinel-async-result"
    fake.assert_called_once()
    kwargs = fake.call_args.kwargs
    assert fake.call_args.args == ("tests.unit.tasks.email",)
    assert kwargs["args"] == (1, 2)
    assert kwargs["kwargs"] == {"subject": "hi"}
    # Default queue resolves from CoreSettings — defaults to "default".
    assert kwargs["queue"] == "default"


def test_enqueue_respects_explicit_queue_override(monkeypatch) -> None:
    fake = MagicMock()
    monkeypatch.setattr(celery_app, "send_task", fake)
    enqueue("tests.unit.tasks.email", queue="email")
    assert fake.call_args.kwargs["queue"] == "email"
