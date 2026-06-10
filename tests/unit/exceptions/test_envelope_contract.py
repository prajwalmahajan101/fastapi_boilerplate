"""Pin the kit → boilerplate exception envelope bridge.

:func:`resilience_kit.testing.verify_envelope_contract` iterates over
every ``ResilienceKitError`` subclass the kit ships and asserts the
handler under test produces a body matching the configured envelope
schema. Fails the test with the complete list of broken classes in one
run — much easier to fix than a one-at-a-time discovery loop, and
catches the case where a kit upgrade adds a new error class that the
bridge has not been taught to translate.
"""

from __future__ import annotations

import asyncio
import json

from resilience_kit.testing import verify_envelope_contract

from src.core.exceptions.handlers import kit_error_handler
from src.core.responses.envelope import ErrorEnvelope


def _call_handler(exc) -> dict:
    """Run the async handler synchronously and return the parsed body.

    ``kit_error_handler`` is an async FastAPI handler that returns a
    :class:`JSONResponse`. The contract verifier expects a plain dict
    body, so this wrapper drives the coroutine to completion and
    decodes the response body.

    Args:
        exc: A :class:`ResilienceKitError` instance to translate.

    Returns:
        The envelope dict the handler would emit on the wire.
    """
    response = asyncio.get_event_loop().run_until_complete(
        kit_error_handler(request=None, exc=exc)  # type: ignore[arg-type]
    )
    return json.loads(response.body)


def test_kit_envelope_contract() -> None:
    """Every kit error class must translate cleanly into ``ErrorEnvelope``."""
    verify_envelope_contract(
        handler=_call_handler,
        envelope_schema=ErrorEnvelope.model_validate,
    )
