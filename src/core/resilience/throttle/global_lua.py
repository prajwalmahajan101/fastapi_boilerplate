"""Process-wide cache for the global-throttle Lua script SHA.

The global-throttle Lua script is loaded into Redis on first use of
:meth:`RedisThrottle.check_fixed_window`; the SHA is then cached at
module level so subsequent calls go straight to ``EVALSHA`` instead of
re-running ``SCRIPT LOAD`` on every invocation. The cache is reset on
``NoScriptError`` (e.g. after a Redis ``SCRIPT FLUSH``) — the caller is
expected to re-load via :func:`load_sha`.

Process-wide rather than per-throttle-instance because the Lua script
itself is process-independent; sharing one SHA also bounds the number
of ``SCRIPT LOAD`` round-trips to one per Redis ``SCRIPT FLUSH``,
regardless of how many throttle backends end up resolving to the
same Redis connection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.resilience.throttle.lua_scripts import GLOBAL_THROTTLE_LUA_SCRIPT

logger = logging.getLogger(__name__)

_sha: str | None = None
_lock = asyncio.Lock()


def get_cached_sha() -> str | None:
    """Return the cached SHA, or ``None`` if not yet loaded."""
    return _sha


async def load_sha(redis_client: Any) -> str:
    """Ensure :data:`GLOBAL_THROTTLE_LUA_SCRIPT` is loaded; return its SHA.

    Args:
        redis_client: An ``aioredis``-style async client.

    Returns:
        The SHA returned by Redis ``SCRIPT LOAD``.
    """
    global _sha
    cached = _sha
    if cached is not None:
        return cached
    async with _lock:
        if _sha is not None:
            return _sha
        _sha = await redis_client.script_load(GLOBAL_THROTTLE_LUA_SCRIPT)
        logger.info("Global throttle Lua loaded (sha=%s)", _sha)
        return _sha


async def reset_sha() -> None:
    """Drop the cached SHA. Used by tests and ``NoScriptError`` recovery."""
    global _sha
    async with _lock:
        _sha = None


__all__ = ["get_cached_sha", "load_sha", "reset_sha"]
