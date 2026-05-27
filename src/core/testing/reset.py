"""One-shot reset for every module-level singleton this process holds.

The application relies on module-level singletons in several places
(``_caches``, ``_throttle``, ``_registry``, the api_log repository, the
fire-and-forget queue registry, …). They make production lifespan
management simple — one init per process — but the same shape is hostile
to tests that need a clean slate between cases.

This module is the single source of truth for "reset every singleton
the process knows about". A test fixture (``conftest.py``) imports
:func:`reset_all_singletons` and awaits it in its teardown hook; no
test ever has to know which modules cache what.

Calls run in dependency order (consumers first, producers last) so an
in-flight reset on one singleton can't observe a half-reset state on
another. New singleton-owning modules MUST add their reset call here
when they land — keeps the test contract explicit.
"""

from __future__ import annotations

from src.core.api_log.factory import _reset_for_tests as _reset_api_log
from src.core.resilience.cache.provider import reset_caches
from src.core.resilience.circuit_breaker.provider import reset_registry
from src.core.resilience.throttle.provider import reset_throttle
from src.core.utils.db import dispose_all_engines
from src.core.utils.fire_and_forget import _reset_registry as _reset_queues
from src.core.utils.redis import close_all_redis_clients


async def reset_all_singletons() -> None:
    """Drop every module-level singleton the process holds.

    Awaitable so callers from ``pytest-asyncio`` fixtures don't need
    to mix sync + async resets. Safe to call when nothing has been
    initialised — every underlying helper is itself idempotent.

    When you add a singleton-owning module (e.g. a domain log repository
    or a strategy factory), add its ``_reset_for_tests`` call here.
    """
    # Domain singletons reset first (consumers): add yours here.
    _reset_api_log()

    # Resilience providers.
    await reset_caches()
    await reset_throttle()
    await reset_registry()

    # Background queues — the registry list is itself a singleton.
    _reset_queues()

    # Infrastructure (close client pools last so anything above that
    # might still be holding a connection has already let go).
    await close_all_redis_clients()
    await dispose_all_engines()
