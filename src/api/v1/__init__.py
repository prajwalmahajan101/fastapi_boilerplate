"""``/api/v1`` router — versioned API surface.

Mount each resource router here. The example ``hello`` + ``items`` routers
ship as a reference; replace them with your own.

The JWT refresh + logout routes (``jwt_router``) are mounted lazily —
they appear in the OpenAPI surface only when ``"jwt"`` is in
``settings.auth_enabled_providers``. Same for the Google OAuth router
when ``"oauth_google"`` is enabled.
"""

from fastapi import APIRouter

from src.api.v1.auth import router as auth_router
from src.api.v1.hello import router as hello_router
from src.api.v1.items import router as items_router
from src.core.runtime import get_settings

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(hello_router, tags=["Example"])
v1_router.include_router(items_router, prefix="/items", tags=["Example"])
v1_router.include_router(auth_router, tags=["Auth"])

_enabled = set(get_settings().auth_enabled_providers or [])

if "jwt" in _enabled:
    # Lazy import: pulls in src.auth.jwt (and therefore PyJWT) only
    # when the deployment actually enables the JWT provider. Same
    # pattern as the oauth_google branch below.
    from src.api.v1.auth_jwt import jwt_router as _auth_jwt_router  # noqa: PLC0415

    v1_router.include_router(_auth_jwt_router, prefix="/auth", tags=["Auth"])

if "oauth_google" in _enabled:
    # Lazy import: Authlib is only imported when this branch runs.
    from src.auth.oauth_google import oauth_router as _oauth_router  # noqa: PLC0415

    v1_router.include_router(_oauth_router, prefix="/auth/google", tags=["Auth"])

__all__ = ["v1_router"]
