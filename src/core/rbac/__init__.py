"""Role-based access control — resource registry + FastAPI dependency.

``register_resource(model_dotted_name, resource)`` lets each domain
module declare which RBAC resource its model belongs to without
editing core code. ``RequireResource(resource, action)`` is the
FastAPI dependency that gates routes; it resolves the authenticated
user via :func:`src.core.auth.api_key.current_user` and checks the
``(resource, action)`` pair against the user's role permissions
(with a per-request cache to avoid duplicate DB hits).
"""

from __future__ import annotations

from src.core.rbac.dependencies import (
    RequireResource,
    set_current_user_dependency,
    user_has_permission,
)
from src.core.rbac.registry import (
    app_resources,
    register_resource,
    registered_mappings,
    resource_for,
)

__all__ = [
    "RequireResource",
    "app_resources",
    "register_resource",
    "registered_mappings",
    "resource_for",
    "set_current_user_dependency",
    "user_has_permission",
]
