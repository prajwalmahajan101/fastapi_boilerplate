"""Throttle scopes — how to derive (identifier, limit, window) from a request.

A scope object is a small callable that, given a FastAPI ``Request``, a
rate string ("100/min"), and the settings, returns the
``(identifier, limit, window_seconds)`` tuple the throttle backend
expects. Five built-in scopes match the Django implementation:

    * ``user_tier`` — pick a per-tier rate (anon/user/admin) from settings.
    * ``burst`` — short window, per (user|IP).
    * ``global`` — single bucket per app or per IP.
    * ``endpoint`` — per (route, user|IP).
    * ``ip`` — per IP only.

Principal resolution is auth-agnostic: if an auth layer stamps
``request.state.system_id`` (and/or ``request.state.api_key_id``) on the
request, ``_request_principal`` reads it so the bucket key follows the
consumer across key rotations. With no auth layer (as in the boilerplate's
default), requests fall through to ``client_ip`` as the bucket label and
resolve to the
``anon`` tier.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.core.runtime import get_settings
from src.core.utils.network import client_ip

if TYPE_CHECKING:
    from fastapi import Request

_UNIT_TO_SECONDS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86_400,
    "day": 86_400,
}


def parse_rate(rate: str) -> tuple[int, int]:
    """Parse a ``"<int>/<unit>"`` rate string into ``(limit, window_seconds)``.

    Accepts short and long unit names (``s``/``sec``/``second``,
    ``m``/``min``/``minute``, ``h``/``hour``, ``d``/``day``).

    Args:
        rate: A rate string such as ``"100/min"`` or ``"5/sec"``.

    Returns:
        Tuple ``(limit, window_seconds)`` ready for the throttle
        backend.

    Raises:
        ValueError: The string doesn't match ``<int>/<unit>`` or the
            unit isn't one of the known ones.
    """
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\w+)\s*", rate)
    if not match:
        raise ValueError(
            f"Invalid rate '{rate}'. Expected '<int>/<unit>' (e.g. '100/min')."
        )
    limit_str, unit = match.groups()
    unit = unit.lower()
    if unit not in _UNIT_TO_SECONDS:
        raise ValueError(
            f"Unknown rate unit '{unit}'. Use s, m, h, or d (or longer aliases)."
        )
    return int(limit_str), _UNIT_TO_SECONDS[unit]


def _request_principal(request: "Request") -> str | None:
    """Return the authenticated principal id, if any.

    Reads ``request.state.system_id`` first, then falls back to
    ``request.state.api_key_id``. An auth layer (if you add one) stamps
    these after a successful credential check. ``system_id`` is preferred
    because it survives key rotation — the bucket key follows the consumer
    across key rotations. Returns ``None`` when neither is present (the
    default in this boilerplate), so callers fall back to client IP.

    Args:
        request: Incoming FastAPI / Starlette request.

    Returns:
        Principal identifier string, or ``None`` when the request is
        unauthenticated.
    """
    system_id = getattr(request.state, "system_id", None)
    if system_id is not None:
        return str(system_id)
    api_key_id = getattr(request.state, "api_key_id", None)
    if api_key_id is not None:
        return str(api_key_id)
    return None


class _BaseScope:
    """Common protocol — every scope provides ``identify(request, rate)``."""

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Return the throttle bucket and parsed rate for ``request``.

        Args:
            request: Incoming request.
            rate: Rate string accepted by :func:`parse_rate`.

        Raises:
            NotImplementedError: Subclasses must override.
        """
        raise NotImplementedError


class UserTierThrottle(_BaseScope):
    """Per-tier rates. Reads ``rate`` from ``rate_limit_<tier>`` setting if "tier"."""

    def __init__(self, default_rate: str | None = None) -> None:
        """Bind an optional fallback rate used when ``rate=="tier"``.

        Args:
            default_rate: Last-resort rate string if no per-tier
                setting is configured.
        """
        self._default_rate = default_rate

    def _tier(self, request: "Request") -> str:
        """Map the request to a coarse tier name (``"anon"`` or ``"user"``).

        Args:
            request: Incoming request.

        Returns:
            ``"anon"`` when there is no authenticated principal;
            ``"user"`` otherwise. Admin tier is a future hook.
        """
        principal = _request_principal(request)
        if principal is None:
            return "anon"
        return "user"

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Resolve a tier-aware ``(identifier, limit, window)`` triple.

        When ``rate`` is the literal ``"tier"`` the helper looks up
        ``rate_limit_<tier>`` from settings, falling back to the
        constructor default, then to ``"60/min"``.

        Args:
            request: Incoming request.
            rate: Rate string or the sentinel ``"tier"``.

        Returns:
            ``(identifier, limit, window_seconds)`` ready for the
            throttle backend; identifier is ``user_tier:<tier>:<principal>``.
        """
        tier = self._tier(request)
        # Allow callers to pass "tier" as a sentinel; resolve from settings.
        effective_rate = rate
        if rate == "tier":
            settings = get_settings()
            rate_field = f"rate_limit_{tier}"
            effective_rate = (
                getattr(settings, rate_field, None) or self._default_rate or "60/min"
            )
        limit, window = parse_rate(effective_rate)
        principal = _request_principal(request) or client_ip(request)
        identifier = f"user_tier:{tier}:{principal}"
        return identifier, limit, window


class BurstThrottle(_BaseScope):
    """Short-window burst limit per (user or IP)."""

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Bucket on the principal (or client IP) without a tier label.

        Args:
            request: Incoming request.
            rate: Rate string accepted by :func:`parse_rate`.

        Returns:
            ``(burst:<principal>, limit, window_seconds)`` triple.
        """
        limit, window = parse_rate(rate)
        principal = _request_principal(request) or client_ip(request)
        return f"burst:{principal}", limit, window


class GlobalThrottle(_BaseScope):
    """Single bucket — per-application limit."""

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Return a singleton ``("global", limit, window)`` triple.

        Args:
            request: Incoming request (unused — bucket is per-app).
            rate: Rate string accepted by :func:`parse_rate`.

        Returns:
            Triple with the literal identifier ``"global"``.
        """
        limit, window = parse_rate(rate)
        return "global", limit, window


class EndpointThrottle(_BaseScope):
    """Per (route, user|IP)."""

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Bucket on the request's route path plus principal/IP.

        Args:
            request: Incoming request.
            rate: Rate string accepted by :func:`parse_rate`.

        Returns:
            ``(endpoint:<route>:<principal>, limit, window)`` triple.
        """
        limit, window = parse_rate(rate)
        route = request.scope.get("path", "<unknown>")
        principal = _request_principal(request) or client_ip(request)
        return f"endpoint:{route}:{principal}", limit, window


class IPThrottle(_BaseScope):
    """Per IP only."""

    def identify(self, request: "Request", rate: str) -> tuple[str, int, int]:
        """Bucket on the resolved client IP only.

        Args:
            request: Incoming request.
            rate: Rate string accepted by :func:`parse_rate`.

        Returns:
            ``(ip:<client_ip>, limit, window)`` triple.
        """
        limit, window = parse_rate(rate)
        return f"ip:{client_ip(request)}", limit, window


SCOPES: dict[str, _BaseScope] = {
    "user_tier": UserTierThrottle(),
    "burst": BurstThrottle(),
    "global": GlobalThrottle(),
    "endpoint": EndpointThrottle(),
    "ip": IPThrottle(),
}


def resolve_scope(scope: str | _BaseScope) -> _BaseScope:
    """Look up a scope object by name, or pass through a custom instance.

    Args:
        scope: Either a built-in scope key (``"user_tier"``, ``"burst"``,
            ``"global"``, ``"endpoint"``, ``"ip"``) or a custom
            ``_BaseScope`` subclass instance.

    Returns:
        The resolved scope object ready to call ``identify`` on.

    Raises:
        ValueError: Unknown scope name.
    """
    if isinstance(scope, _BaseScope):
        return scope
    if scope not in SCOPES:
        raise ValueError(
            f"Unknown throttle scope '{scope}'. Known: {sorted(SCOPES.keys())}."
        )
    return SCOPES[scope]
