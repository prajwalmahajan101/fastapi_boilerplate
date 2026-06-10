"""Application enums.

Add your domain enums here as ``StrEnum`` subclasses — they serialise to
their string value in JSON and compare equal to plain strings, which keeps
API payloads and DB columns readable.

``RequestDirection`` is re-exported from ``src.core.api_log`` so callers
can ``from src.common.enums import RequestDirection`` without reaching
into core internals. ``src.common`` may import from ``src.core``; the
dependency rule only forbids the reverse.
"""

from __future__ import annotations

from enum import StrEnum

# Re-export core enums consumers use frequently.
from src.core.api_log import RequestDirection


class Environment(StrEnum):
    """Deployment environment — example domain enum.

    Matched against ``settings.app_environment`` in places that branch on
    "is this dev/test" (e.g. relaxing a guard). Replace or extend with the
    enums your application actually needs.
    """

    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class Resource(StrEnum):
    """RBAC resource families guarded by ``RequireResource``.

    Extend per-domain; the registry in ``src.core.rbac.registry``
    looks up ``(resource, action)`` pairs against the authenticated
    user's role permissions. Stored as plain strings in the DB so
    adding a value here does not require a column-type migration.
    """

    ACCOUNT = "account"
    ROLE = "role"
    API_KEY = "api_key"
    ITEM = "item"


class Action(StrEnum):
    """RBAC actions guarded by ``RequireResource``."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


__all__ = [
    "Action",
    "Environment",
    "RequestDirection",
    "Resource",
]
