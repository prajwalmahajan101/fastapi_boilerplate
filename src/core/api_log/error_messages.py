"""Error-message composition for ``api_logs.error_message``.

Single concern: turn an exception into the pipe-delimited summary string
the audit log persists. Lives in its own module so the API-error
inspection (which has to deferred-import ``src.core.exceptions.api`` to
avoid a cycle) doesn't drag the import into the decorator hot path.
"""

from __future__ import annotations


def build_error_message(exc: Exception) -> str:
    """Compose a single-line error string from ``exc`` for the audit row.

    For ``APIError`` subclasses, also folds in ``status_code``,
    ``response_body``, and ``details`` so the audit row carries the full
    upstream context.

    Args:
        exc: The exception raised by the wrapped handler.

    Returns:
        Pipe-delimited summary string, e.g.
        ``"upstream rejected … | status_code=502 | response_body=..."``.
    """
    from src.core.exceptions.api import APIError

    parts: list[str] = [str(exc)]
    if isinstance(exc, APIError):
        if exc.status_code is not None:
            parts.append(f"status_code={exc.status_code}")
        if exc.response_body:
            parts.append(f"response_body={exc.response_body}")
        if exc.details:
            parts.append(f"details={exc.details}")
    return " | ".join(parts)


__all__ = ["build_error_message"]
