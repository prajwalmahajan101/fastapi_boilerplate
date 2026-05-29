"""Authentication primitives — API key dependency, key issuance helper.

Pulls the ``X-API-Key`` header off an inbound request, validates the
prefix + secret in constant time, and yields the bound ``User`` (with
eager-loaded ``roles.permissions``) to downstream dependencies. The
matching writer-side helper :func:`generate_api_key` returns an opaque
URL-safe token whose first eight characters are also the lookup
prefix.
"""

from __future__ import annotations

from src.auth.api_key import (
    current_user,
    current_user_optional,
    generate_api_key,
)

# Late-bind RBAC's "current user" hook so ``src.core.rbac`` does not
# import ``src.auth`` (the one-way layering rule). Routes can then
# ``Depends(RequireResource(..., ...))`` and the resolver fires our
# X-API-Key flow without any per-route plumbing.
from src.core.rbac.dependencies import set_current_user_dependency

set_current_user_dependency(current_user)

__all__ = ["current_user", "current_user_optional", "generate_api_key"]
