"""``@log_inbound_request`` / ``@log_outbound_request`` — public re-exports.

The actual implementations live in :mod:`src.core.api_log.inbound` and
:mod:`src.core.api_log.outbound`; pure helpers (sanitizers, error-message
composition) in :mod:`src.core.api_log.sanitizers` /
:mod:`.error_messages`; the bounded background queue in
:mod:`.dispatch`. This module keeps the historical import path stable
for existing callers (``from src.core.api_log import log_inbound_request``).

Inbound: decorate a FastAPI route handler that takes ``request: Request``::

    @router.post("/webhook")
    @log_inbound_request(service_name="webhook")
    async def webhook(request: Request, payload: WebhookSchema):
        ...

Outbound: decorate any service method that calls ``AsyncAPIClient``. The
client publishes the full HTTP metadata to ``outbound_response_meta_ctx``
right before returning, so the decorator does not need to inspect the
request manually::

    @log_outbound_request(service_name="payments_api")
    @retry_with_exponential_backoff(max_retries=2, exceptions=(APIError,))
    async def charge(amount): ...
"""

from __future__ import annotations

from src.core.api_log.inbound import log_inbound_request
from src.core.api_log.outbound import log_outbound_request

__all__ = ["log_inbound_request", "log_outbound_request"]
