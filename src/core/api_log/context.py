"""ContextVar that ``AsyncAPIClient._request`` uses to publish per-call HTTP metadata.

``@log_outbound_request`` (defined in ``core.api_log.decorators``) reads
from this var so the decorator can sit on a *service* method (above the
HTTP call) and still capture the full request/response shape.

The dict published by ``AsyncAPIClient`` matches the ignosis shape::

    {
        "method", "url", "params",
        "request_headers", "request_body_json", "request_body_data",
        "status_code", "response_headers", "response_body",
    }
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

outbound_response_meta_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "outbound_response_meta", default=None
)
