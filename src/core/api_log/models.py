"""``ApiLog`` Pydantic model + ``RequestDirection`` enum.

Lives in core because the audit-log infrastructure is project-independent
— the model describes *any* HTTP call, inbound or outbound. Common code
can re-export ``RequestDirection`` from ``src.common.enums`` if domain
modules want a single canonical import path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RequestDirection(StrEnum):
    """Whether an API call was received (INBOUND) or made by the app (OUTBOUND)."""

    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class ApiLog(BaseModel):
    """One inbound or outbound API call recorded in the audit log."""

    # Identity
    id: int | None = None
    log_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    direction: RequestDirection
    service_name: str
    request_id: str | None = None
    environment: str | None = None

    # Request
    method: str
    url: str
    query_params: dict[str, Any] | None = None
    request_headers: dict[str, str] | None = None
    request_body: str | None = None

    # Response
    response_status_code: int | None = None
    response_headers: dict[str, str] | None = None
    response_body: str | None = None

    # Timing
    duration_ms: float | None = None

    # Error
    error_type: str | None = None
    error_message: str | None = None

    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_expires_at: int | None = None
    extra: dict[str, Any] | None = None
