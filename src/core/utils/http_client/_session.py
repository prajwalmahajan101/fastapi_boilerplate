"""Loop-aware shared ``aiohttp.ClientSession`` lifecycle.

Pulled out of ``AsyncAPIClient`` so the orchestrator no longer mixes
session-lifecycle concerns with request dispatch / auth / error
mapping. The manager keeps the same class-level shape (one process-wide
session, lazily created, lock-guarded), but the loop-ownership
transition is no longer silent — a different event loop asking for the
session emits a single warning before the state is reset.

That matters when a long-lived worker thread spins its own loop while
the FastAPI loop is also alive: the existing code silently dropped the
prior loop's session on the floor; now operators see the transition in
the log stream so the footgun is at least visible.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from src.core.utils.logging import get_logger

logger = get_logger(__name__)


class SessionManager:
    """Process-wide ``aiohttp.ClientSession`` keyed to a single event loop.

    The session and its connector are shared across every caller in the
    process for keep-alive reuse. State lives at the class level so the
    public ``AsyncAPIClient`` orchestrator can call into the manager
    statically without threading an instance through every method.

    Loop-ownership invariant: at most one event loop owns the cached
    session at a time. If a different loop appears, the class state is
    reset before the new loop creates a fresh session — and a warning
    is emitted, since this only happens in pytest-asyncio per-test loop
    fixtures or (the dangerous case) a worker thread spinning its own
    loop.
    """

    _session: ClassVar[Any] = None
    _connector: ClassVar[Any] = None
    _session_lock: ClassVar[Any] = None  # lazily bound to the current loop
    _owner_loop_id: ClassVar[int | None] = None  # id() of the loop that owns _session

    @classmethod
    def _reset_class_state(cls) -> None:
        """Drop session/connector/lock references without awaiting close."""
        cls._session = None
        cls._connector = None
        cls._session_lock = None
        cls._owner_loop_id = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Return the session lock, creating it on first call.

        Creating the lock lazily binds it to the event loop active when
        ``get_session`` first runs — tests that spin a new loop per
        case can ``reset`` between cases without inheriting a lock from
        a closed loop.

        Returns:
            The shared ``asyncio.Lock`` guarding session creation.
        """
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    @classmethod
    async def get_session(cls) -> Any:
        """Return the shared ``aiohttp.ClientSession``, creating it lazily.

        On every call the current event loop is compared against the
        loop that owns the cached session. A mismatch logs a warning
        (so the loop-ownership reset is auditable) and drops the class
        state before a fresh session is created on the new loop.

        Connection-pool limits are 100 total / 30 per host with a 5-min
        DNS cache.

        Returns:
            An ``aiohttp.ClientSession`` ready for HTTP calls.
        """
        import aiohttp

        current_loop_id = id(asyncio.get_running_loop())
        if (
            cls._owner_loop_id is not None
            and cls._owner_loop_id != current_loop_id
        ):
            logger.warning(
                "HTTP session loop changed; resetting shared session state. "
                "This is expected under pytest-asyncio per-test loops but "
                "in production indicates a worker spinning its own loop.",
                extra={
                    "previous_loop_id": cls._owner_loop_id,
                    "current_loop_id": current_loop_id,
                },
            )
            cls._reset_class_state()

        async with cls._get_lock():
            if cls._session is None or cls._session.closed:
                # Custom resolver consults the per-task DNS pin
                # populated by AsyncAPIClient.request after the SSRF
                # validator runs. Without this pin, aiohttp does its
                # own getaddrinfo at dispatch time and a malicious
                # zone could return a different (private) IP than the
                # one the validator approved (the classic
                # DNS-rebinding TOCTOU). The pin is per-asyncio-task
                # so concurrent requests don't trample each other.
                from src.core.utils.http_client._dns_pin import (  # noqa: PLC0415
                    PinnedResolver,
                )

                cls._connector = aiohttp.TCPConnector(
                    limit=100,
                    limit_per_host=30,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True,
                    resolver=PinnedResolver(),
                )
                cls._session = aiohttp.ClientSession(
                    connector=cls._connector,
                    connector_owner=True,
                )
                cls._owner_loop_id = current_loop_id
                logger.info(
                    "Created HTTP session with pooling",
                    extra={
                        "total_limit": 100,
                        "per_host_limit": 30,
                        "dns_cache_ttl": 300,
                    },
                )
            return cls._session

    @classmethod
    async def close_session(cls) -> None:
        """Close the shared session and clear loop-bound class state.

        Paired with the FastAPI lifespan shutdown so a process restart
        does not leak the connector pool. The lock and owner-loop id
        are also cleared so the next loop creates a fresh lock —
        important for per-test loop fixtures.
        """
        if cls._session is not None and not cls._session.closed:
            await cls._session.close()
            logger.info("Closed HTTP session.")
        cls._reset_class_state()

    @classmethod
    def reset(cls) -> None:
        """Test helper — drop class state without awaiting session close.

        Use when a test fixture cannot ``await close_session`` (e.g. a
        sync teardown). The session/connector references are dropped on
        the floor; aiohttp will garbage-collect them when the
        ``event_loop`` they belong to is closed.
        """
        cls._reset_class_state()


__all__ = ["SessionManager"]
