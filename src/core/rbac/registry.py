"""Resource ↔ Model registry for RBAC.

Domain modules populate this registry at import time so adding a new
app does not require editing the auth core. Every app registers its
own ``Resource`` mappings via :func:`register_resource` and the
authentication / authorization code reads them through
:func:`resource_for`. New domain modules drop in next to existing
ones without any cross-package source edit.

Resources are stored as plain strings so this module never has to
import a concrete enum (and the layering rule never gets bent —
``src.common.enums.Resource`` is a ``StrEnum`` so its values pass
through as strings transparently).
"""

from __future__ import annotations

from threading import Lock

_lock = Lock()
_RESOURCE_FOR_MODEL: dict[str, str] = {}


def register_resource(model_dotted_name: str, resource: str) -> None:
    """Register a ``"package.model_name"`` → ``Resource`` mapping.

    Idempotent: re-registering the same mapping is a no-op.
    Re-registering the same key with a different resource raises
    :class:`ValueError` because a model can only own one RBAC resource
    — silent overwrites would cause subtle permission drift.

    Args:
        model_dotted_name: Lower-cased ``"<package>.<model_name>"``
            identifier (matches Django's ``Permission.codename``
            convention).
        resource: The string value from
            :class:`src.common.enums.Resource`.

    Raises:
        ValueError: When the same key is already mapped to a different
            resource value.
    """
    key = model_dotted_name.lower()
    with _lock:
        existing = _RESOURCE_FOR_MODEL.get(key)
        if existing is None:
            _RESOURCE_FOR_MODEL[key] = resource
            return
        if existing != resource:
            raise ValueError(
                f"register_resource({key!r}): already mapped to "
                f"{existing!r}, refusing to overwrite with {resource!r}."
            )


def resource_for(package: str, model_name: str) -> str | None:
    """Return the resource registered for ``"<package>.<model_name>"``.

    Args:
        package: Package name (e.g. ``"auth"``).
        model_name: Model class name (lowercased).

    Returns:
        The matching resource string, or ``None`` when unregistered.
    """
    return _RESOURCE_FOR_MODEL.get(f"{package}.{model_name}".lower())


def app_resources(package: str) -> list[str]:
    """Return every resource registered for any model in ``package``."""
    prefix = f"{package}.".lower()
    return [
        resource
        for key, resource in _RESOURCE_FOR_MODEL.items()
        if key.startswith(prefix)
    ]


def registered_mappings() -> dict[str, str]:
    """Snapshot the full mapping (defensive copy, for diagnostics / tests)."""
    return dict(_RESOURCE_FOR_MODEL)


def _reset_for_tests() -> None:
    """Drop every mapping. Used between test cases."""
    with _lock:
        _RESOURCE_FOR_MODEL.clear()


__all__ = [
    "app_resources",
    "register_resource",
    "registered_mappings",
    "resource_for",
]
