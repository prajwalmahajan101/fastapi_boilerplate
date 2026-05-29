"""Unit-tier conftest.

Unit tests run with **no external services** — no Postgres, no Redis,
no network. The autouse marker injected by the root conftest tags
everything collected under ``tests/unit/`` with ``@pytest.mark.unit``,
so the tier is selectable via ``pytest -m unit`` without contributors
having to remember to apply the marker manually.

Add fixtures here that are reusable across unit sub-areas (e.g.
factory helpers, fake-clock fixtures). Per-area fixtures belong in a
local ``conftest.py`` inside the leaf directory.
"""

from __future__ import annotations
