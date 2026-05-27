"""Resilience layer — retry, circuit breaker, cache, throttle.

Cache / throttle / breaker each expose a process-wide singleton
through a small provider module (``cache/provider.py``,
``throttle/provider.py``, ``circuit_breaker/provider.py``). Each
provider also exposes a public ``reset_*`` coroutine; the central
test-reset surface at ``src.core.testing.reset_all_singletons`` calls
all three, so tests never need to know which modules cache what.
"""

from src.core.resilience.decorators import circuit_breaker, resilient
from src.core.resilience.registry import resilience_registry
from src.core.resilience.retry import retry_on_failure, retry_with_exponential_backoff

__all__ = [
    "circuit_breaker",
    "resilience_registry",
    "resilient",
    "retry_on_failure",
    "retry_with_exponential_backoff",
]
