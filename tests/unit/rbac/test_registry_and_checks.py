"""Unit tests for the RBAC registry + ``user_has_permission`` check.

The real ``User`` ORM is not exercised here — we use lightweight
stand-ins that match the duck type ``user_has_permission`` reads
(``has_superuser_role`` flag + ``roles[*].permissions[*]``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import Request
from starlette.requests import Request as StarletteRequest

from src.common.enums import Action, Resource
from src.core.rbac import registry
from src.core.rbac.dependencies import user_has_permission


@dataclass
class _Perm:
    resource: str
    action: str


@dataclass
class _Role:
    permissions: list[_Perm] = field(default_factory=list)
    is_superuser_role: bool = False


@dataclass
class _User:
    roles: list[_Role] = field(default_factory=list)

    @property
    def has_superuser_role(self) -> bool:
        return any(r.is_superuser_role for r in self.roles)


def _make_request() -> Request:
    """Build a minimal Starlette ``Request`` for the cache path."""
    scope = {"type": "http", "headers": [], "method": "GET", "path": "/"}
    return StarletteRequest(scope)


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry._reset_for_tests()


def test_register_resource_is_idempotent_for_same_value() -> None:
    registry.register_resource("foo.bar", "bar")
    registry.register_resource("foo.bar", "bar")
    assert registry.resource_for("foo", "bar") == "bar"


def test_register_resource_conflicts_raise() -> None:
    registry.register_resource("foo.bar", "bar")
    with pytest.raises(ValueError):
        registry.register_resource("foo.bar", "other")


def test_app_resources_filters_by_package() -> None:
    registry.register_resource("acct.user", "account")
    registry.register_resource("acct.api_key", "api_key")
    registry.register_resource("items.item", "item")

    assert sorted(registry.app_resources("acct")) == ["account", "api_key"]


def test_user_with_no_roles_is_denied() -> None:
    assert user_has_permission(_User(), Resource.API_KEY, Action.READ) is False


def test_superuser_bypasses_check() -> None:
    user = _User(roles=[_Role(is_superuser_role=True)])
    # No permission rows, but superuser flag short-circuits.
    assert user_has_permission(user, Resource.API_KEY, Action.DELETE) is True


def test_explicit_permission_grants_access() -> None:
    user = _User(
        roles=[_Role(permissions=[_Perm(Resource.API_KEY, Action.READ)])]
    )
    assert user_has_permission(user, Resource.API_KEY, Action.READ) is True
    assert user_has_permission(user, Resource.API_KEY, Action.DELETE) is False


def test_permission_cache_lives_on_request_state() -> None:
    request = _make_request()
    user = _User(
        roles=[_Role(permissions=[_Perm(Resource.API_KEY, Action.READ)])]
    )

    # First call populates the cache.
    assert user_has_permission(
        user, Resource.API_KEY, Action.READ, request=request
    ) is True
    cache = request.state._permission_cache
    assert (Resource.API_KEY.value, Action.READ.value) in cache

    # Mutating the underlying user does not change the cached answer.
    user.roles = []
    assert user_has_permission(
        user, Resource.API_KEY, Action.READ, request=request
    ) is True


def test_none_user_always_denies() -> None:
    assert user_has_permission(None, Resource.API_KEY, Action.READ) is False
