"""Circuit breaker — Redis-backed primary + in-memory fallback."""

from src.core.resilience.circuit_breaker.base import (
    BaseCircuitBreaker,
    BaseCircuitBreakerRegistry,
    CircuitBreakerConfig,
    CircuitState,
)
from src.core.resilience.circuit_breaker.memory_impl import (
    InMemoryCircuitBreaker,
    InMemoryRegistry,
)
from src.core.resilience.circuit_breaker.provider import get_registry, reset_registry
from src.core.resilience.circuit_breaker.redis_impl import (
    RedisCircuitBreaker,
    RedisRegistry,
)

__all__ = [
    "BaseCircuitBreaker",
    "BaseCircuitBreakerRegistry",
    "CircuitBreakerConfig",
    "CircuitState",
    "InMemoryCircuitBreaker",
    "InMemoryRegistry",
    "RedisCircuitBreaker",
    "RedisRegistry",
    "get_registry",
    "reset_registry",
]
