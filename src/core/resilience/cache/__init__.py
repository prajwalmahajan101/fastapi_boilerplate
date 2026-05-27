"""Cache subsystem — Redis primary + in-memory fallback + versioned utils."""

from src.core.resilience.cache.base import BaseCacheBackend
from src.core.resilience.cache.memory_impl import InMemoryCacheBackend
from src.core.resilience.cache.provider import get_cache, reset_caches
from src.core.resilience.cache.redis_impl import RedisCacheBackend
from src.core.resilience.cache.utils import (
    CacheVersionError,
    bump_dataset_cache_version,
    generate_cache_key,
    get_cached_result,
    get_dataset_cache_version,
    set_cached_result,
)

__all__ = [
    "BaseCacheBackend",
    "CacheVersionError",
    "InMemoryCacheBackend",
    "RedisCacheBackend",
    "bump_dataset_cache_version",
    "generate_cache_key",
    "get_cache",
    "get_cached_result",
    "get_dataset_cache_version",
    "reset_caches",
    "set_cached_result",
]
