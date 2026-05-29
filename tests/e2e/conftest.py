"""E2E-tier conftest.

E2E tests drive the **full FastAPI app** through ``TestClient`` with the
application lifespan engaged — so the real resilience providers
(Postgres-backed or Redis-backed, with in-memory fallback) and the real
audit-log backend are wired up exactly as production would have them.

Tests live here only when they need to assert across multiple layers
(middleware → route → service → repository → audit). A test that only
needs the middleware/envelope contract belongs under ``tests/unit/``;
a test that only exercises one backing store belongs under
``tests/integration/``.

The default :func:`client` fixture in the root conftest does **not**
engage the lifespan. The :func:`live_client` fixture below does — use
it when a test depends on the real startup wiring.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.app import app


@pytest.fixture
def live_client() -> Iterator[TestClient]:
    """Return a ``TestClient`` that runs the application lifespan.

    Use this fixture when a test needs the real startup wiring
    (resilience providers connected to their real backends, audit-log
    backend, runtime-bound settings). For pure middleware/envelope
    smoke tests prefer the lifespan-less :func:`client` from the root
    conftest — it's faster and needs no services.

    Yields:
        A ``TestClient`` bound to the app with its lifespan engaged.
    """
    with TestClient(app) as test_client:
        yield test_client
