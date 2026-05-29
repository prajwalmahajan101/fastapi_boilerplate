"""Data manipulation utilities — projection, sanitisation, type coercion.

These are intentionally tiny one-liners promoted to named functions so
the call sites read self-documenting. Service / repository code reaches
for these often enough that the duplication adds up.
"""

from __future__ import annotations

from typing import Any

from src.core.exceptions.validation import ValidationError


def filter_dict_keys(
    rows: list[dict[str, Any]], keys: list[str], *, strict: bool = False
) -> list[dict[str, Any]]:
    """Project each dict in ``rows`` to the subset of fields in ``keys``.

    Args:
        rows: List of dicts to project. Returned as-is if ``keys`` is empty.
        keys: Subset of keys to retain.
        strict: When ``True``, raise ``KeyError`` if any row is missing a
            requested key. When ``False`` (default), missing keys are
            silently dropped from that row's output.

    Returns:
        A new list of dicts containing only the requested keys.

    Raises:
        KeyError: When ``strict`` is ``True`` and a row lacks a key.
    """
    if not keys:
        return rows
    if strict:
        return [{k: row[k] for k in keys} for row in rows]
    return [{k: row.get(k) for k in keys if k in row} for row in rows]


def sanitize_string(value: str, *, max_length: int = 1000) -> str:
    """Enforce a maximum length on a string value.

    Does NOT perform character escaping — parameter values are passed
    to database drivers via parameterised queries which handle escaping
    internally, and to log sanitisers via
    :mod:`src.core.utils.log_sanitization` which masks them
    independently.

    Args:
        value: Raw input string.
        max_length: Maximum allowed length (defaults to 1000).

    Returns:
        ``value`` unchanged when it fits the limit.

    Raises:
        ValidationError: When ``value`` exceeds ``max_length``.
    """
    if len(value) > max_length:
        raise ValidationError(
            f"String too long: {len(value)} chars (max: {max_length})."
        )
    return value


def parse_bool(value: Any) -> bool:
    """Permissive boolean coercion for already-typed values.

    Use this for values that may arrive as ``bool`` / ``int`` / ``str``
    (e.g. settings overlays from a generic dict, JSON payloads where
    booleans were stringified). For raw query parameters prefer the
    typed coercion in :func:`src.core.utils.filters.extract_filters`,
    which raises ``ValidationError`` on bad input instead of silently
    returning ``False``.

    Args:
        value: Anything whose stringification can be interpreted as a
            boolean.

    Returns:
        ``True`` for ``True`` / ``"true"`` / ``"1"`` / ``"yes"`` / ``"on"``;
        ``False`` for everything else.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


__all__ = ["filter_dict_keys", "parse_bool", "sanitize_string"]
