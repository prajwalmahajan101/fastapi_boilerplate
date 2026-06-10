"""Per-environment profile selection via ``APP_ENV``."""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_settings():
    sys.modules.pop("src.common.settings", None)
    return importlib.import_module("src.common.settings")


@pytest.mark.parametrize(
    "env,expected",
    [
        ("local", "LocalSettings"),
        ("test", "TestSettings"),
        ("bogus", "LocalSettings"),
    ],
)
def test_profile_selection(monkeypatch, env, expected):
    monkeypatch.setenv("APP_ENV", env)
    mod = _reload_settings()
    assert type(mod.settings).__name__ == expected


def test_prod_profile_fails_on_localhost_db(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    with pytest.raises(Exception) as exc_info:
        _reload_settings()
    assert "DB_HOST" in str(exc_info.value)
