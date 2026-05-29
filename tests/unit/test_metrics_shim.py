"""Unit tests for the cardinality contract in ``src.core.metrics``."""

from __future__ import annotations

import logging

import pytest

from src.core import metrics


@pytest.mark.parametrize("key", sorted(metrics._FORBIDDEN_LABEL_KEYS))
def test_forbidden_label_keys_reject(key: str) -> None:
    """Every key on the forbidden list must raise at the call site."""
    with pytest.raises(metrics.CardinalityViolation):
        metrics.record_counter("test_event", **{key: "anything"})


def test_unknown_label_key_rejects() -> None:
    """A label that is neither forbidden nor on the allow-list is rejected."""
    with pytest.raises(metrics.CardinalityViolation):
        metrics.record_counter("test_event", region="ap-south-1")


@pytest.mark.parametrize(
    "key",
    # ``event`` is the positional event-name parameter on the recorder
    # APIs themselves, not a bounded label keyword — skip it here.
    sorted(metrics._BOUNDED_LABEL_KEYS - {"event"}),
)
def test_bounded_label_keys_accept(key: str) -> None:
    """Keys on the allow-list never raise."""
    metrics.record_counter("test_event", **{key: "value"})


def test_record_duration_emits_structured_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The shim emits one INFO log per call with the structured payload."""
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    metrics.record_duration("http_request", 12.5, status="ok", subsystem="cache")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.event == "http_request"  # type: ignore[attr-defined]
    assert record.metric == "duration"  # type: ignore[attr-defined]
    assert record.duration_ms == 12.5  # type: ignore[attr-defined]
    assert record.status == "ok"  # type: ignore[attr-defined]
    assert record.subsystem == "cache"  # type: ignore[attr-defined]


def test_record_counter_default_n_is_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    metrics.record_counter("hits")
    assert caplog.records[0].n == 1  # type: ignore[attr-defined]


def test_record_gauge_carries_value(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="src.core.metrics")
    metrics.record_gauge("queue_depth", 42.0)
    assert caplog.records[0].value == 42.0  # type: ignore[attr-defined]
