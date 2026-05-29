"""Unit tests for ``capture_and_dispatch`` + ``persist_log``."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.api_log import dispatch as dispatch_mod
from src.core.api_log.dispatch import (
    CaptureState,
    capture_and_dispatch,
    persist_log,
)
from src.core.api_log.models import ApiLog, RequestDirection


def _stub_log() -> ApiLog:
    """Return a minimally-populated ``ApiLog`` for repository round-trips."""
    return ApiLog(
        direction=RequestDirection.INBOUND,
        service_name="test",
        request_id=None,
        environment="test",
        method="GET",
        url="http://t",
    )


async def test_capture_returns_handler_result_on_success(monkeypatch) -> None:
    """The wrapped callable's return value is propagated unchanged."""
    submitted: list[Any] = []
    monkeypatch.setattr(dispatch_mod, "fire_and_forget", submitted.append)

    async def handler() -> str:
        return "ok"

    captured: list[CaptureState] = []

    def build_log(state: CaptureState) -> ApiLog:
        captured.append(state)
        return _stub_log()

    out = await capture_and_dispatch(handler, (), {}, build_log)
    assert out == "ok"
    assert len(captured) == 1
    assert captured[0].exc is None
    assert captured[0].result == "ok"
    assert captured[0].elapsed_ms >= 0.0


async def test_capture_re_raises_handler_exception(monkeypatch) -> None:
    """The original exception type is re-raised after audit is queued."""
    monkeypatch.setattr(dispatch_mod, "fire_and_forget", lambda _coro: None)

    async def handler() -> None:
        raise ValueError("boom")

    captured: list[CaptureState] = []

    def build_log(state: CaptureState) -> ApiLog:
        captured.append(state)
        return _stub_log()

    with pytest.raises(ValueError, match="boom"):
        await capture_and_dispatch(handler, (), {}, build_log)

    assert len(captured) == 1
    assert isinstance(captured[0].exc, ValueError)


async def test_capture_swallows_builder_failure_preserves_handler_exception(
    monkeypatch,
) -> None:
    """Regression test for ISSUE-016 — builder bug must not mask state.exc."""
    submitted: list[Any] = []
    monkeypatch.setattr(dispatch_mod, "fire_and_forget", submitted.append)

    async def handler() -> None:
        raise ValueError("original")

    def broken_builder(state: CaptureState) -> ApiLog:
        raise RuntimeError("builder bug")

    # The caller must see the original ValueError, NOT the RuntimeError
    # from the builder.
    with pytest.raises(ValueError, match="original"):
        await capture_and_dispatch(handler, (), {}, broken_builder)

    # And nothing was submitted to the queue because the build failed.
    assert submitted == []


async def test_persist_log_swallows_repository_errors(monkeypatch) -> None:
    """A repository.save() that raises must not propagate to the producer."""

    class _BoomRepo:
        async def save(self, log: ApiLog) -> None:
            raise RuntimeError("db down")

    import src.core.api_log.factory as factory_mod

    monkeypatch.setattr(factory_mod, "get_repository", lambda: _BoomRepo())

    # Must not raise.
    await persist_log(_stub_log())
