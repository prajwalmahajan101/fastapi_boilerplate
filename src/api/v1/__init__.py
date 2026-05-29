"""``/api/v1`` router — versioned API surface.

Mount each resource router here. The example ``hello`` + ``items`` routers
ship as a reference; replace them with your own.
"""

from fastapi import APIRouter

from src.api.v1.auth import router as auth_router
from src.api.v1.hello import router as hello_router
from src.api.v1.items import router as items_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(hello_router, tags=["Example"])
v1_router.include_router(items_router, prefix="/items", tags=["Example"])
v1_router.include_router(auth_router, tags=["Auth"])

__all__ = ["v1_router"]
