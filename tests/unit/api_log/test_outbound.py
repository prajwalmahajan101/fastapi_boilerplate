"""Unit tests for outbound audit-row construction (`_build_outbound_log`).

Focuses on the ``extra`` diagnostic column: HTTP/idempotency plumbing
kwargs are filtered out, while genuine diagnostic context is retained.
"""

from __future__ import annotations

from src.core.api_log.models import RequestDirection
from src.core.api_log.outbound import _build_outbound_log
from src.core.api_log.sanitizers import UNSET


def _build(**func_kwargs: object):
    """Build an outbound ApiLog from ``func_kwargs`` with empty call state."""
    return _build_outbound_log(
        func_kwargs=dict(func_kwargs),
        service_name="payments_api",
        result=UNSET,
        duration_ms=1.0,
        meta=None,
        exc=None,
        exc_type=None,
        exc_msg=None,
    )


def test_idempotency_kwargs_excluded_from_extra() -> None:
    """The idempotency plumbing kwargs never leak into the ``extra`` column."""
    log = _build(
        method="post",
        url="https://api.example.com/disburse",
        json={"amount": 500},
        auto_idempotency_key=True,
        idempotency_key="abc123",
        loan_id="L-42",
    )
    assert log.direction == RequestDirection.OUTBOUND
    assert log.extra == {"loan_id": "L-42"}
    assert "idempotency_key" not in log.extra
    assert "auto_idempotency_key" not in log.extra


def test_extra_is_none_when_only_http_kwargs() -> None:
    """With no diagnostic kwargs, ``extra`` collapses to ``None``."""
    log = _build(
        method="post",
        url="https://api.example.com/x",
        auto_idempotency_key=True,
    )
    assert log.extra is None
