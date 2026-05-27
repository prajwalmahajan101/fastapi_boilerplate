"""Top-level ``/api`` router — mounts health probes and the versioned API."""

from fastapi import APIRouter

from src.api.health import build_api_health_router
from src.api.v1 import v1_router

api_router = APIRouter(prefix="/api")
api_router.include_router(build_api_health_router(), tags=["Health"])
api_router.include_router(v1_router)

__all__ = ["api_router"]
