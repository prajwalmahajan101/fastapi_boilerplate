"""Shared pytest fixtures for the boilerplate test-suite.

Two of these mirror what the application lifespan does — minus the external
I/O — so tests need neither Postgres nor Redis:

* :func:`_bind_settings` calls ``core.runtime.configure(settings)`` once for
  the session, exactly as the lifespan's first startup step does, so any
  code reading ``get_settings()`` sees the same config the app would.
* :func:`_reset_singletons` drops every module-level singleton after each
  test via :func:`resilience_kit.testing.reset_all_singletons_async`,
  so cache / throttle / circuit-breaker / api-log state never leaks
  across cases.

The :func:`client` fixture constructs ``TestClient(app)`` *without* entering
its lifespan context: the resilience providers lazily fall back to their
in-memory backends and the api-log backend to its no-op, which is all a
smoke test needs. Tests that exercise real Postgres/Redis behaviour should
spin up those services and enter the lifespan explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resilience_kit.testing import reset_all_singletons_async

from src.app import app
from src.common.settings import settings
from src.core.runtime import configure
from src.core.runtime import reset as reset_runtime

_TIER_MARKERS = ("unit", "integration", "e2e")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-tag each collected test with its tier marker by directory.

    A test under ``tests/<tier>/...`` gets ``@pytest.mark.<tier>`` so
    contributors can subset with ``pytest -m unit`` / ``-m integration``
    / ``-m e2e`` without remembering to decorate each module.

    Args:
        config: The active pytest configuration (unused).
        items: The collected test items, mutated in place.
    """
    del config
    tests_root = Path(__file__).resolve().parent
    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(tests_root)
        except ValueError:
            continue
        if not rel.parts:
            continue
        tier = rel.parts[0]
        if tier in _TIER_MARKERS:
            item.add_marker(getattr(pytest.mark, tier))


@pytest.fixture(scope="session", autouse=True)
def _bind_settings() -> Iterator[None]:
    """Bind the application settings to ``core.runtime`` for the whole session.

    Yields:
        Control while the session runs; clears the binding on teardown.
    """
    configure(settings)
    yield
    reset_runtime()


@pytest.fixture(autouse=True)
async def _reset_singletons() -> AsyncIterator[None]:
    """Drop every cached process singleton after each test.

    Yields:
        Control while the test runs; resets all singletons afterwards so
        the next test starts from a clean slate.
    """
    yield
    await reset_all_singletons_async()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Return a ``TestClient`` that does not run the application lifespan.

    Yields:
        A client bound to the boilerplate app. Resilience providers fall
        back to in-memory and the api-log backend to its no-op, so no
        Postgres/Redis is required.
    """
    test_client = TestClient(app)
    yield test_client
    test_client.close()
