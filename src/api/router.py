"""Full URL tree — assembled from the root probes + the ``/api`` namespace.

The root probes (``/healthz`` / ``/readyz``) are kept outside ``/api`` so
kubernetes / load-balancer configurations that hardcode those paths don't
need to know about the API prefix. The mirror ``/api/health`` /
``/api/readiness`` lives under the API namespace for client tooling.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api import api_router
from src.api.health import build_root_health_router

root_router = APIRouter()
root_router.include_router(build_root_health_router(), tags=["Health"])
root_router.include_router(api_router)

__all__ = ["root_router"]
