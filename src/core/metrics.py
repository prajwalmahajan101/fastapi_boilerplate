"""Metrics shim — uniform entry point for duration / counter / gauge events.

Today every call fans out to ``logger.info`` with a structured ``extra=``
payload that log-aggregation can pick up. The Prometheus exporter swap
(adding ``prometheus-client`` and tee-ing into Histograms / Counters /
Gauges) is a one-line change inside :func:`record_duration` /
:func:`record_counter` / :func:`record_gauge` once the dependency
lands. No call site changes.

Cardinality contract — DO NOT bypass:

  Bounded labels (safe for metrics + logs):
      event        — enum-like, ~20 values
      subsystem    — cache / breaker / throttle / outbox / dispatch
      status       — ok / error
      outcome      — success / timeout / breaker_open / permission_denied
      backend      — redis / memory / redis-fallback / postgres / noop

  Unbounded — LOGS ONLY, never metrics:
      request_id, user_id, item_id, partner_id, raw URLs

  Forbidden in both: PII, raw error messages, free-form text.

The contract is enforced at runtime by :func:`_assert_bounded` so a
wrong call site is caught the first time it is exercised, not after
the ``prometheus-client`` install lands.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Allow-list. Add a key here ONLY if it has a documented, bounded value
# space. The cardinality contract is the source of truth.
_BOUNDED_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "event",
        "subsystem",
        "status",
        "outcome",
        "backend",
    }
)

# Hard rejection list. These are the obvious unbounded identifiers a
# future contributor might reach for; rejecting them by name (instead of
# relying on the allow-list alone) yields a clearer error at the call
# site.
_FORBIDDEN_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "request_id",
        "user_id",
        "item_id",
        "partner_id",
        "url",
        "path",
        "error",
        "error_message",
        "email",
        "phone",
        "pan",
        "aadhaar",
    }
)


class CardinalityViolation(ValueError):
    """Raised when a metrics call site passes a label that would blow up
    Prometheus' time-series space.

    Catching this at runtime is intentional — a metric registered with
    high-cardinality labels can take down the scrape endpoint long
    before a human notices. Fail-fast at the call site instead.

    Deliberately exempt from the ``BaseCustomError`` hierarchy: this is
    a programmer error caught synchronously by tests and CI gates,
    never reaching the FastAPI exception handler. Subclassing
    ``ValueError`` lets call sites use the stdlib idiom
    ``except ValueError`` if they want to recover.
    """


def _assert_bounded(labels: dict[str, Any]) -> None:
    """Reject unbounded or forbidden label keys.

    Empty dicts are fine. A label is acceptable when:
      * it is in :data:`_BOUNDED_LABEL_KEYS`, AND
      * it is not in :data:`_FORBIDDEN_LABEL_KEYS` (defence in depth —
        the two sets are kept disjoint by construction, but the
        explicit check yields a clearer error if someone adds to both).

    Args:
        labels: Caller-supplied bounded labels.

    Raises:
        CardinalityViolation: When any key is forbidden or unknown.
    """
    for key in labels:
        if key in _FORBIDDEN_LABEL_KEYS:
            raise CardinalityViolation(
                f"metrics label {key!r} is forbidden (unbounded cardinality). "
                f"Pass it via the log `extra=` payload instead."
            )
        if key not in _BOUNDED_LABEL_KEYS:
            raise CardinalityViolation(
                f"metrics label {key!r} is not in the bounded allow-list "
                f"{sorted(_BOUNDED_LABEL_KEYS)}. If this value is truly "
                f"low-cardinality, add it to _BOUNDED_LABEL_KEYS."
            )


def record_duration(
    event: str,
    duration_ms: float,
    *,
    status: str = "ok",
    **bounded_labels: str,
) -> None:
    """Record a duration sample for ``event``.

    Today: emits one ``INFO`` log with a structured payload. Tomorrow:
    ALSO updates ``prometheus_client.Histogram(name=f"app_{event}_duration_seconds")``.

    Args:
        event: enum-like event name (matches a ``log_duration`` event).
        duration_ms: measured duration in milliseconds.
        status: ``ok`` or ``error``.
        **bounded_labels: see the cardinality contract — only keys in
            :data:`_BOUNDED_LABEL_KEYS` are accepted.

    Raises:
        CardinalityViolation: When a label key violates the contract.
    """
    _assert_bounded(bounded_labels)
    logger.info(
        "%s metric",
        event,
        extra={
            "metric": "duration",
            "event": event,
            "duration_ms": duration_ms,
            "status": status,
            **bounded_labels,
        },
    )


def record_counter(
    event: str,
    *,
    status: str = "ok",
    n: int = 1,
    **bounded_labels: str,
) -> None:
    """Record ``n`` occurrences of ``event``.

    Today: emits one ``INFO`` log. Tomorrow: ALSO increments
    ``prometheus_client.Counter(name=f"app_{event}_total")``.

    Args:
        event: enum-like event name.
        status: ``ok`` or ``error``.
        n: Number of occurrences to record (defaults to 1).
        **bounded_labels: see the cardinality contract.
    """
    _assert_bounded(bounded_labels)
    logger.info(
        "%s counter",
        event,
        extra={
            "metric": "counter",
            "event": event,
            "n": n,
            "status": status,
            **bounded_labels,
        },
    )


def record_gauge(
    name: str,
    value: float,
    **bounded_labels: str,
) -> None:
    """Record the current value of a gauge.

    Today: emits one ``INFO`` log. Tomorrow: ALSO sets
    ``prometheus_client.Gauge(name=f"app_{name}").set(value)``.

    Use for slow-moving state (outbox depth, breaker state, degraded
    backend count).

    Args:
        name: enum-like gauge name.
        value: Current value to record.
        **bounded_labels: see the cardinality contract.
    """
    _assert_bounded(bounded_labels)
    logger.info(
        "%s gauge",
        name,
        extra={
            "metric": "gauge",
            "event": name,
            "value": value,
            **bounded_labels,
        },
    )


__all__ = [
    "CardinalityViolation",
    "record_counter",
    "record_duration",
    "record_gauge",
]
