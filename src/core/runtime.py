"""Core runtime binding.

Holds the active ``CoreSettings`` instance so core modules can read config
without importing from ``src.common`` (the dependency rule: core never
imports common). The application calls ``configure(settings)`` exactly
once at startup; everything else in core reads the bound instance via
``get_settings()``.
"""

from __future__ import annotations

from src.core.settings import CoreSettings

_settings: CoreSettings | None = None


def configure(settings: CoreSettings) -> None:
    """Bind a ``CoreSettings`` instance. Idempotent — last call wins.

    Args:
        settings: The ``CoreSettings`` instance the app wants core to read.
    """
    global _settings
    _settings = settings


def get_settings() -> CoreSettings:
    """Return the bound settings, falling back to defaults if unconfigured.

    Falling back instead of raising lets modules import safely at startup
    before the app has called ``configure()`` (e.g. test collection,
    decorator resolution); behaviour matches a vanilla ``CoreSettings()``
    construction so nothing crashes.

    Returns:
        The bound ``CoreSettings`` instance, or a fresh default if unset.
    """
    if _settings is None:
        return CoreSettings()
    return _settings


def reset() -> None:
    """Test helper — clear the bound settings instance."""
    global _settings
    _settings = None
