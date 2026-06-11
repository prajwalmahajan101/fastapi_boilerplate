"""FastAPI ``RequireResource`` dependency.

Usage::

    @router.get(
        "/items",
        dependencies=[Depends(RequireResource(Resource.ITEM, Action.READ))],
    )
    async def list_items(...): ...

The dependency resolves the authenticated user via the "current user"
hook registered through :func:`set_current_user_dependency`, then
checks whether the user holds the ``(resource, action)`` pair via
:func:`user_has_permission`. Results cache on
``request.state._permission_cache`` for the lifetime of the request so
multiple gates within one handler do not duplicate the lookup.

The auth package (outside ``src.core``) calls
:func:`set_current_user_dependency` at import time so this module
never imports a domain package directly — the one-way layering rule
is preserved.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db.dependencies import get_session
from src.core.exceptions.auth import PermissionDeniedError

_current_user_dep: Callable[..., Awaitable[Any]] | None = None


def set_current_user_dependency(dep: Callable[..., Awaitable[Any]]) -> None:
    """Register the FastAPI-style async dependency that resolves the user.

    Called once at application import time by ``src.auth`` (the package
    that owns the X-API-Key flow). The registered callable must be
    safe to use as a FastAPI ``Depends`` target — typically an
    ``async def`` function with FastAPI dependency parameters.

    Args:
        dep: The async user resolver (e.g.
            ``src.auth.api_key.current_user``).
    """
    global _current_user_dep
    _current_user_dep = dep


async def _resolve_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Forward to the registered resolver.

    The session is resolved through FastAPI's dependency pipeline and
    forwarded explicitly: the resolver is invoked as a plain coroutine
    here, so a ``Depends(get_session)`` default on the registered
    callable would never be unpacked.

    Raises:
        RuntimeError: When no ``current_user`` dependency has been
            registered yet. This means the auth package was not
            imported at startup; either import ``src.auth`` from the
            app factory or remove the ``RequireResource`` dependency.
    """
    if _current_user_dep is None:
        raise RuntimeError(
            "RequireResource: no current-user dependency registered. "
            "Import src.auth at application startup or call "
            "src.core.rbac.set_current_user_dependency(...) before "
            "any route runs."
        )
    return await _current_user_dep(request, session)


def user_has_permission(
    user: Any,
    resource: str,
    action: str,
    *,
    request: Request | None = None,
) -> bool:
    """Canonical resource/action permission check.

    Single source of truth for "does *user* hold (resource, action)?".
    Used by :class:`RequireResource` and by any call site that needs to
    enforce a permission outside FastAPI's dependency pipeline. The
    same per-request cache shape is reused when ``request`` is supplied
    so dependency-level and in-handler checks share cache entries.

    Args:
        user: The authenticated user with eager-loaded
            ``roles.permissions``. ``None`` always denies.
        resource: Resource string (Resource enum or raw str).
        action: Action string (Action enum or raw str).
        request: Active request — when provided, the cache lives on
            ``request.state._permission_cache``.

    Returns:
        ``True`` when the user holds the pair (or has a superuser
        role); ``False`` otherwise.
    """
    if user is None:
        return False
    if getattr(user, "has_superuser_role", False):
        return True

    cache: dict[tuple[str, str], bool] | None = None
    if request is not None:
        cache = getattr(request.state, "_permission_cache", None)
        if cache is None:
            cache = {}
            request.state._permission_cache = cache
        cache_key = (str(resource), str(action))
        if cache_key in cache:
            return cache[cache_key]

    granted = False
    for role in getattr(user, "roles", []) or []:
        for perm in getattr(role, "permissions", []) or []:
            if perm.resource == resource and perm.action == action:
                granted = True
                break
        if granted:
            break

    if cache is not None:
        cache[(str(resource), str(action))] = granted
    return granted


class RequireResource:
    """Dependency class — check ``(resource, action)`` for the current user."""

    def __init__(self, resource: str, action: str) -> None:
        """Bind the dependency to a specific resource + action."""
        self.resource = str(resource)
        self.action = str(action)

    async def __call__(
        self,
        request: Request,
        user: Any = Depends(_resolve_current_user),
    ) -> Any:
        """Resolve the current user and enforce the permission check.

        Args:
            request: Active FastAPI request.
            user: The authenticated user from the registered resolver.

        Returns:
            The authenticated user (handlers can ``Depends`` on the
            same instance to grab the user reference).

        Raises:
            PermissionDeniedError: When the user does not hold the pair.
        """
        if not user_has_permission(user, self.resource, self.action, request=request):
            raise PermissionDeniedError(
                f"Missing permission: {self.resource}:{self.action}"
            )
        return user


__all__ = [
    "RequireResource",
    "set_current_user_dependency",
    "user_has_permission",
]
