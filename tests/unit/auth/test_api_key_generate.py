"""Unit tests for ``src.auth.api_key.generate_api_key``.

The full ``current_user`` flow needs a real DB session and the
``APIKey`` model wired up — those live under integration tests. Here
we cover the helper that mints the token.
"""

from __future__ import annotations

import string

import pytest

from src.auth.api_key import generate_api_key


def test_generate_api_key_returns_raw_and_prefix() -> None:
    raw, prefix = generate_api_key()
    assert isinstance(raw, str) and isinstance(prefix, str)
    assert len(prefix) == 8
    assert raw.startswith(prefix)


def test_generated_keys_are_unique() -> None:
    keys = {generate_api_key()[0] for _ in range(50)}
    assert len(keys) == 50


@pytest.mark.parametrize("_", range(5))
def test_key_only_contains_url_safe_characters(_: int) -> None:
    raw, _prefix = generate_api_key()
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert set(raw) <= allowed
