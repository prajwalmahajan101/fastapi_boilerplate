"""High-level cache helpers — deterministic keys + dataset versioning.

Fail-open semantics: a missing or broken backend never raises out of
``get_cached_result`` / ``set_cached_result``. ``bump_dataset_cache_version``
will raise ``CacheVersionError`` only if the backend cannot reliably
implement the atomic ``add+incr`` dance.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.core.resilience.cache.provider import get_cache

logger = logging.getLogger(__name__)

_CACHE_KEY_VERSION = 1
_DATASET_VERSION_PREFIX = "dataset_cache_version"


class CacheVersionError(Exception):
    """Backend cannot reliably bump a version counter."""


def _serialize_params(params: dict[str, Any] | None) -> str:
    """Render ``params`` as a stable JSON string (sorted keys, no spaces).

    Stable ordering is what makes the cache key deterministic — two
    callers that pass equivalent dicts hit the same key.

    Args:
        params: Optional bag of parameters to fold into the key.

    Returns:
        ``"{}"`` when empty/None, otherwise the JSON encoding.
    """
    if not params:
        return "{}"
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def generate_cache_key(
    prefix: str,
    query: str,
    params: dict[str, Any] | None = None,
    user_id: int | str | None = None,
    datasource_id: int | None = None,
) -> str:
    """Build a deterministic cache key from ``query``, ``params``, and identity.

    Returns ``"<prefix>:v<CACHE_KEY_VERSION>:<sha256>"``. Bumping
    ``_CACHE_KEY_VERSION`` invalidates every key across the cluster
    without having to enumerate them.

    Args:
        prefix: Logical namespace (e.g. ``"catalog:items"`` or similar).
        query: Caller-supplied query identifier.
        params: Optional parameter bag, folded into the hash via
            :func:`_serialize_params`.
        user_id: Optional identity scoping (per-user caches).
        datasource_id: Optional datasource scoping (per-dataset caches).

    Returns:
        Cache key suitable for Redis / in-memory backends.
    """
    components = [f"query={query}", f"params={_serialize_params(params)}"]
    if user_id is not None:
        components.append(f"user={user_id}")
    if datasource_id is not None:
        components.append(f"ds={datasource_id}")
    digest = hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()
    return f"{prefix}:v{_CACHE_KEY_VERSION}:{digest}"


async def get_cached_result(cache_key: str, *, alias: str = "default") -> Any | None:
    """Fetch ``cache_key`` — returns ``None`` on miss or any backend error.

    Fail-open: a backend outage is logged and treated as a miss, so a
    cache server going down never breaks the caller's path.

    Args:
        cache_key: Key produced by :func:`generate_cache_key`.
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        Cached value, or ``None`` on miss / backend error.
    """
    try:
        cache = await get_cache(alias)
        result = await cache.get(cache_key)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Cache GET failed (failing open as miss): %s",
            cache_key[:80],
            exc_info=True,
        )
        return None
    return result


async def set_cached_result(
    cache_key: str,
    value: Any,
    ttl: int,
    *,
    alias: str = "default",
) -> None:
    """Store ``value`` under ``cache_key`` — silent on any backend error.

    Mirrors :func:`get_cached_result`'s fail-open semantics; a failed
    write is logged but never raises.

    Args:
        cache_key: Key produced by :func:`generate_cache_key`.
        value: Any JSON-serialisable payload supported by the backend.
        ttl: Lifetime in seconds.
        alias: Cache backend alias from ``redis_urls`` config.
    """
    try:
        cache = await get_cache(alias)
        await cache.set(cache_key, value, ttl=ttl)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Cache SET failed (silent): %s",
            cache_key[:80],
            exc_info=True,
        )


def _dataset_version_key(dataset_id: int) -> str:
    """Compose the version-counter key for a dataset.

    Args:
        dataset_id: Numeric dataset identifier.

    Returns:
        The fully-qualified version counter key for that dataset.
    """
    return f"{_DATASET_VERSION_PREFIX}:{dataset_id}"


async def get_dataset_cache_version(dataset_id: int, *, alias: str = "default") -> int:
    """Return the current cache version for ``dataset_id`` (0 if unset).

    Args:
        dataset_id: Numeric dataset identifier.
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        Current integer version, or ``0`` when no counter exists or
        the backend errors out (fail-open).
    """
    try:
        cache = await get_cache(alias)
        version = await cache.get(_dataset_version_key(dataset_id))
    except Exception:  # noqa: BLE001
        logger.warning(
            "dataset_cache_version GET failed (treating as 0): %d",
            dataset_id,
            exc_info=True,
        )
        return 0
    return int(version) if version is not None else 0


async def bump_dataset_cache_version(dataset_id: int, *, alias: str = "default") -> int:
    """Atomically bump the version, initialising to 1 if not present.

    Tries ``INCR`` first; on missing-key, ``ADD 1`` (atomic create);
    retries ``INCR`` once if a racy peer created the key between the
    two steps. Surfaces the rare third-failure case as
    :class:`CacheVersionError`.

    Args:
        dataset_id: Numeric dataset identifier.
        alias: Cache backend alias from ``redis_urls`` config.

    Returns:
        The new version number.

    Raises:
        CacheVersionError: The backend cannot reliably bump the
            counter (e.g. ADD+INCR race lost twice in a row).
    """
    cache = await get_cache(alias)
    key = _dataset_version_key(dataset_id)

    try:
        new_version = await cache.incr(key)
        logger.info(
            "Bumped dataset cache version to %d (dataset=%d)", new_version, dataset_id
        )
        return new_version
    except ValueError:
        pass

    if await cache.add(key, 1):
        logger.info("Initialised dataset cache version to 1 (dataset=%d)", dataset_id)
        return 1

    try:
        new_version = await cache.incr(key)
        logger.info(
            "Bumped dataset cache version to %d (dataset=%d, retry)",
            new_version,
            dataset_id,
        )
        return new_version
    except ValueError as exc:
        raise CacheVersionError(
            f"Failed to bump cache version for dataset {dataset_id}: "
            "key disappeared between add() and incr(). Check cache backend health."
        ) from exc
