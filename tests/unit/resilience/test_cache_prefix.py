"""Cache key prefix — Redis tier prepends the configured prefix."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.resilience.cache.redis_impl import RedisCacheBackend


class _FakeRedis:
    """Minimal Redis stub recording the exact keys it sees."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.seen_keys: list[str] = []

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Any:
        self.seen_keys.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False):
        self.seen_keys.append(key)
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.seen_keys.append(key)
        self.store.pop(key, None)

    async def exists(self, key: str) -> int:
        self.seen_keys.append(key)
        return 1 if key in self.store else 0

    async def incr(self, key: str) -> int:
        self.seen_keys.append(key)
        new = int(self.store.get(key, 0)) + 1
        self.store[key] = new
        return new


@pytest.mark.asyncio
async def test_redis_cache_prefixes_keys():
    fake = _FakeRedis()
    cache = RedisCacheBackend(fake, key_prefix="svc-prod")

    await cache.set("hello", "world")
    await cache.get("hello")
    await cache.delete("hello")

    assert all(k.startswith("svc-prod:") for k in fake.seen_keys)


@pytest.mark.asyncio
async def test_two_prefixes_dont_collide():
    fake = _FakeRedis()
    a = RedisCacheBackend(fake, key_prefix="svc-a")
    b = RedisCacheBackend(fake, key_prefix="svc-b")

    await a.set("k", "from-a")
    await b.set("k", "from-b")
    assert await a.get("k") == "from-a"
    assert await b.get("k") == "from-b"


@pytest.mark.asyncio
async def test_empty_prefix_is_transparent():
    fake = _FakeRedis()
    cache = RedisCacheBackend(fake, key_prefix="")
    await cache.set("hello", "world")
    assert fake.seen_keys == ["hello"]
