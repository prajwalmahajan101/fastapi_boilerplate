"""Shared ``BackendHealth`` enum for every Redis-primary resilience helper.

The cache (``RedisCacheBackend``), throttle (``RedisThrottle``), and
circuit-breaker (``RedisCircuitBreaker``) implementations each track
whether they're currently serving from Redis or have fallen back to an
embedded in-memory store. The vocabulary is the same in all three
places — promoted to one enum so the operational language matches the
code.

* ``ACTIVE`` — Redis is reachable and serving every operation.
* ``DEGRADED`` — Redis is unreachable; the embedded in-memory backend
  is serving. Each helper has its own recovery path
  (``is_healthy()`` from the readiness probe + an in-call probe) that
  flips back to ``ACTIVE`` on the next successful ``PING``.
"""

from __future__ import annotations

from enum import StrEnum


class BackendHealth(StrEnum):
    """State of a Redis-primary resilience helper's connection to Redis."""

    ACTIVE = "active"
    DEGRADED = "degraded"


__all__ = ["BackendHealth"]
