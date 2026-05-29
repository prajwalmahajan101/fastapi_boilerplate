"""Static contract test for the ``src.auth.jwt`` public surface.

Handlers in ``src/api/v1/auth.py`` import a fixed set of names that
must remain public. This test fails the build if anyone hides them
behind an underscore or removes them from ``__all__``. ISSUE-025.
"""

from __future__ import annotations

from src.auth import jwt as jwt_module

PUBLIC_NAMES = {
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
    "BlacklistOutcome",
    "JWTProvider",
    "blacklist_jti",
    "check_blacklist",
    "decode_token",
    "load_active_user",
    "mint_access_token",
    "mint_refresh_token",
    "mint_token_pair",
}


def test_public_names_exist_on_module() -> None:
    """Each handler-consumed name is attribute-accessible without underscore."""
    missing = sorted(n for n in PUBLIC_NAMES if not hasattr(jwt_module, n))
    assert missing == [], f"src.auth.jwt is missing public names: {missing}"


def test_public_names_listed_in_all() -> None:
    """``__all__`` advertises every handler-consumed name."""
    advertised = set(jwt_module.__all__)
    missing = sorted(PUBLIC_NAMES - advertised)
    assert missing == [], f"src.auth.jwt.__all__ is missing: {missing}"


def test_legacy_underscore_aliases_still_resolve() -> None:
    """Back-compat: the underscore names alias the public ones."""
    assert jwt_module._ACCESS_TOKEN_TYPE is jwt_module.ACCESS_TOKEN_TYPE
    assert jwt_module._REFRESH_TOKEN_TYPE is jwt_module.REFRESH_TOKEN_TYPE
    assert jwt_module._check_blacklist is jwt_module.check_blacklist
    assert jwt_module._load_active_user is jwt_module.load_active_user
