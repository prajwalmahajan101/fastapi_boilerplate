"""Authentication primitives — pluggable provider registry.

Each concrete provider (``api_key``, ``jwt``, ``oauth_google``)
implements :class:`src.auth.base.AuthProvider` and self-registers at
import time. The composite :func:`current_user` /
:func:`current_user_optional` dependencies live in
:mod:`src.auth.registry` and walk ``settings.auth_enabled_providers``
in order — the first provider that returns an
:class:`~src.auth.base.AuthResult` wins.

Routes import the dependency exactly as before:

    from src.auth import current_user

Which providers run is purely a configuration choice; routes do not
need to know.
"""

from __future__ import annotations

# Side-effect import — ``api_key`` self-registers ``APIKeyProvider``
# on first import. Ordering is irrelevant; ``enabled_providers()``
# honours the settings list, not import order.
from src.auth import api_key as _api_key  # noqa: F401
from src.auth.api_key import generate_api_key
from src.auth.base import AuthProvider, AuthResult
from src.auth.registry import (
    current_user,
    current_user_optional,
    enabled_providers,
    register,
    registered_names,
    unregister,
)
from src.core.runtime import get_settings

# Lazy-load optional providers based on configuration. ``jwt`` pulls in
# PyJWT; ``oauth_google`` pulls in Authlib. Deployments that skip a
# provider also skip its import (and dependency) cost.
_enabled = set(get_settings().auth_enabled_providers or [])
if "jwt" in _enabled:
    from src.auth import jwt as _jwt  # noqa: F401, PLC0415
if "oauth_google" in _enabled:
    from src.auth import oauth_google as _oauth_google  # noqa: F401, PLC0415

# Late-bind RBAC's "current user" hook so ``src.core.rbac`` does not
# import ``src.auth`` (the one-way layering rule). Routes can then
# ``Depends(RequireResource(..., ...))`` and the composite resolver
# fires whichever providers the deployment has enabled.
from src.core.rbac.dependencies import set_current_user_dependency  # noqa: E402

set_current_user_dependency(current_user)

__all__ = [
    "AuthProvider",
    "AuthResult",
    "current_user",
    "current_user_optional",
    "enabled_providers",
    "generate_api_key",
    "register",
    "registered_names",
    "unregister",
]
