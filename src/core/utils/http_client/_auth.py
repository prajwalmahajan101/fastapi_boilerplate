"""Outbound HTTP authentication helpers.

``AuthType`` and the pure header / Basic-auth builders previously lived
inside ``AsyncAPIClient``. Splitting them out keeps the orchestrator
focused on dispatch + lifecycle and makes the auth-header shape
unit-testable without spinning a fake aiohttp session.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from src.core.exceptions.validation import ValidationError

if TYPE_CHECKING:
    import aiohttp


class AuthType(StrEnum):
    """Outbound HTTP authentication style (not user authentication)."""

    BEARER = "Bearer"
    BASIC = "Basic"
    API_KEY = "ApiKey"
    NONE = "None"


def build_headers(
    auth_token: str | dict[str, str] | None,
    auth_type: AuthType,
    headers: dict[str, str] | None,
) -> dict[str, str]:
    """Compose the outbound ``Authorization`` header from ``auth_type``.

    ``BEARER`` and the catch-all branch produce ``Authorization``;
    ``API_KEY`` writes a configurable header name (default
    ``x-api-key``) from the ``auth_token`` dict; ``BASIC`` is delegated
    to aiohttp's ``auth=`` parameter so no header is added here;
    ``NONE`` leaves the input headers alone.

    Args:
        auth_token: String for ``BEARER`` / fallback; dict for
            ``API_KEY`` / ``BASIC``; ignored for ``NONE``.
        auth_type: Which authentication scheme to apply.
        headers: Caller-supplied headers; copied so the input is
            never mutated.

    Returns:
        The merged headers dict ready to pass to aiohttp.
    """
    final = headers.copy() if headers else {}
    if auth_type == AuthType.BEARER and isinstance(auth_token, str):
        final["Authorization"] = f"Bearer {auth_token}"
    elif auth_type == AuthType.API_KEY and isinstance(auth_token, dict):
        header_name = auth_token.get("header_name", "x-api-key")
        api_key = auth_token.get("api_key", "")
        if api_key:
            final[header_name] = api_key
    elif auth_type == AuthType.NONE:
        pass
    elif auth_type == AuthType.BASIC:
        # aiohttp handles Basic via the auth= parameter; no header here.
        pass
    elif auth_token:
        final["Authorization"] = f"{auth_type.value} {auth_token}"
    return final


def build_basic_auth(
    auth_token: str | dict[str, str] | None,
    auth_type: AuthType,
) -> aiohttp.BasicAuth | None:
    """Return an ``aiohttp.BasicAuth`` when *auth_type* is ``BASIC``.

    Args:
        auth_token: Expected to be a dict carrying ``username`` and
            ``password`` keys for ``BASIC``; ignored otherwise.
        auth_type: Authentication scheme; non-``BASIC`` returns ``None``.

    Returns:
        ``aiohttp.BasicAuth(username, password)`` for ``BASIC``, else
        ``None`` so the caller can pass ``auth=None`` directly.

    Raises:
        ValidationError: ``BASIC`` selected but ``auth_token`` is not a
            dict, or is missing either ``username`` or ``password``.
    """
    import aiohttp

    if auth_type != AuthType.BASIC:
        return None
    if not isinstance(auth_token, dict):
        raise ValidationError(
            "Basic Auth requires a dict with 'username' and 'password'",
            details={"auth_token_type": type(auth_token).__name__},
        )
    username = auth_token.get("username")
    password = auth_token.get("password")
    if not username or not password:
        raise ValidationError(
            "Basic Auth requires both 'username' and 'password'",
            details={"provided_keys": list(auth_token.keys())},
        )
    return aiohttp.BasicAuth(username, password)


__all__ = ["AuthType", "build_basic_auth", "build_headers"]
