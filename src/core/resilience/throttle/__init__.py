"""Rate limiting — Redis sliding window primary + in-memory fallback."""

from src.core.resilience.throttle.base import BaseThrottle, ThrottleResult
from src.core.resilience.throttle.dependencies import rate_limit
from src.core.resilience.throttle.memory_impl import InMemoryThrottle
from src.core.resilience.throttle.provider import get_throttle, reset_throttle
from src.core.resilience.throttle.redis_impl import RedisThrottle
from src.core.resilience.throttle.scopes import (
    BurstThrottle,
    EndpointThrottle,
    GlobalThrottle,
    IPThrottle,
    UserTierThrottle,
)

__all__ = [
    "BaseThrottle",
    "BurstThrottle",
    "EndpointThrottle",
    "GlobalThrottle",
    "IPThrottle",
    "InMemoryThrottle",
    "RedisThrottle",
    "ThrottleResult",
    "UserTierThrottle",
    "get_throttle",
    "rate_limit",
    "reset_throttle",
]
