"""Hello-world route — the smallest end-to-end example.

Demonstrates three things every route in this codebase uses:

* the standard :func:`SuccessResponse` envelope,
* a ``rate_limit`` dependency (per-endpoint sliding window),
* inbound request auditing via ``@log_inbound_request`` (the handler must
  declare ``request: Request`` for the decorator to read headers/body).

Delete this module once you have real routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.common.openapi_metadata import DEFAULT_RESPONSES
from src.core.api_log import log_inbound_request
from resilience_kit.adapters.fastapi import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse

router = APIRouter()


class HelloData(BaseModel):
    """Payload shape for the hello response envelope."""

    message: str


@router.get(
    "/hello",
    summary="Say hello",
    response_model=SuccessEnvelope[HelloData],
    dependencies=[Depends(rate_limit("endpoint", "60/min"))],
    responses={**DEFAULT_RESPONSES},
)
@log_inbound_request(service_name="example_api")
async def hello(request: Request, name: str = "world"):
    """Return a greeting wrapped in the standard success envelope.

    Args:
        request: Incoming request (read by the audit decorator).
        name: Who to greet; defaults to ``world``.

    Returns:
        A ``JSONResponse`` success envelope with ``data={"message": ...}``.
    """
    return SuccessResponse(
        data={"message": f"Hello, {name}!"},
        message="Greeting generated.",
    )


__all__ = ["router"]
