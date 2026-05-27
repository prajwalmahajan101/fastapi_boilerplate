"""Test-time utilities — currently the singleton reset surface.

Imported only from test fixtures and conftest. Production code should
never depend on anything under ``src.core.testing``.
"""

from src.core.testing.reset import reset_all_singletons

__all__ = ["reset_all_singletons"]
