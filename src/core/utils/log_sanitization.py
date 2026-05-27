"""Logging sanitization utilities.

Guards every log line against three classes of problem:
    * log injection — escapes newlines, carriage returns, tabs, and other
      control characters so attacker-controlled fields cannot forge log
      records;
    * sensitive data leaks — masks any value whose key matches the
      ``password|secret|token|key|auth|credential|api_key|bearer|jwt``
      pattern;
    * memory pressure — truncates large strings, dicts, and lists so a
      pathological payload cannot swamp the log pipeline.

Configurable via ``CoreSettings.log_sanitize_*`` knobs read through
``core.runtime.get_settings()`` — keeps core project-independent.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.runtime import get_settings

_SENSITIVE_PATTERN: re.Pattern[str] = re.compile(
    r"password|secret|token|key|auth|credential|api_key|bearer|jwt",
    re.IGNORECASE,
)
_MASK_VALUE = "***REDACTED***"
_MAX_DEPTH = 5


def _config() -> tuple[int, int, int]:
    """Read the three sanitisation caps from the bound settings.

    Returns:
        Tuple of ``(max_string, max_dict_keys, max_list_items)``.
    """
    s = get_settings()
    return (
        s.log_sanitize_max_string,
        s.log_sanitize_max_dict_keys,
        s.log_sanitize_max_list_items,
    )


def sanitize_for_log(
    value: Any,
    max_string_length: int | None = None,
    max_dict_keys: int | None = None,
    max_list_items: int | None = None,
) -> Any:
    """Recursively sanitize *value* for safe inclusion in a log record.

    Args:
        value: Any Python value to be logged.
        max_string_length: Override the default per-string truncation cap.
        max_dict_keys: Override the default dict-key cap.
        max_list_items: Override the default list-item cap.

    Returns:
        A structurally similar value with control chars escaped,
        sensitive keys masked, and oversized containers truncated.
    """
    cfg_str, cfg_keys, cfg_items = _config()
    return _sanitize(
        value,
        max_string_length or cfg_str,
        max_dict_keys or cfg_keys,
        max_list_items or cfg_items,
        depth=0,
    )


def _sanitize(
    value: Any, max_str: int, max_keys: int, max_items: int, depth: int
) -> Any:
    """Type-dispatch recursive sanitiser. Stops at ``_MAX_DEPTH``.

    Args:
        value: Current value being sanitised.
        max_str: Maximum string length retained.
        max_keys: Maximum dict keys retained.
        max_items: Maximum iterable items retained.
        depth: Current recursion depth.

    Returns:
        Sanitised mirror of ``value``; ``"<max depth exceeded>"`` past
        the depth cap; ``"<bytes: N bytes>"`` for raw bytes.
    """
    if depth > _MAX_DEPTH:
        return "<max depth exceeded>"
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_string(value, max_str)
    if isinstance(value, bytes):
        return f"<bytes: {len(value)} bytes>"
    if isinstance(value, dict):
        return _sanitize_dict(value, max_str, max_keys, max_items, depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        return _sanitize_iterable(value, max_str, max_keys, max_items, depth)
    try:
        return _sanitize_string(str(value), max_str)
    except Exception:
        return f"<{type(value).__name__}: unserializable>"


def _sanitize_string(value: str, max_length: int) -> str:
    r"""Escape control characters and truncate the string.

    Backslashes, newlines, carriage returns, and tabs become literal
    escapes; any other ``ord(ch) < 32`` byte becomes ``\xNN``. Strings
    longer than ``max_length`` are middle-trimmed with a length
    indicator so a glance at the log still shows both ends.

    Args:
        value: Raw string to sanitise.
        max_length: Length above which middle-trim kicks in.

    Returns:
        Escaped (and possibly truncated) version of ``value``.
    """
    out = (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    out = "".join(ch if ord(ch) >= 32 else f"\\x{ord(ch):02x}" for ch in out)
    if len(out) > max_length:
        half = (max_length - 20) // 2
        out = f"{out[:half]}...{out[-half:]} ({len(value)} chars)"
    return out


def _sanitize_dict(
    value: dict[str, Any],
    max_str: int,
    max_keys: int,
    max_items: int,
    depth: int,
) -> dict[str, Any]:
    """Mask sensitive keys, recurse into values, cap the total key count.

    Sensitive key names (``password``, ``token``, ``secret`` …) match
    the module-level regex; their values are replaced with the redaction
    marker before recursion. Dicts beyond ``max_keys`` get a synthetic
    ``__truncated__`` entry recording the dropped count.

    Args:
        value: Dict to sanitise.
        max_str: Maximum string length passed through.
        max_keys: Maximum keys retained from this dict.
        max_items: Maximum items retained from any nested iterables.
        depth: Current recursion depth.

    Returns:
        New dict with masked / truncated / sanitised entries.
    """
    result: dict[str, Any] = {}
    keys = list(value.keys())
    truncated = len(keys) > max_keys
    for i, key in enumerate(keys):
        if i >= max_keys:
            break
        str_key = str(key)
        if _SENSITIVE_PATTERN.search(str_key):
            result[str_key] = _MASK_VALUE
        else:
            result[str_key] = _sanitize(
                value[key], max_str, max_keys, max_items, depth + 1
            )
    if truncated:
        result["__truncated__"] = f"{len(keys) - max_keys} more keys"
    return result


def _sanitize_iterable(
    value: list | tuple | set | frozenset,
    max_str: int,
    max_keys: int,
    max_items: int,
    depth: int,
) -> list[Any]:
    """Cap an iterable at ``max_items`` and recurse into each element.

    Args:
        value: Iterable to sanitise.
        max_str: Maximum string length passed through.
        max_keys: Maximum keys retained from any nested dicts.
        max_items: Maximum items retained from this iterable.
        depth: Current recursion depth.

    Returns:
        A list (even for tuple / set inputs) of sanitised items,
        possibly ending with an ``"...and N more items"`` marker.
    """
    items = list(value)
    truncated = len(items) > max_items
    result: list[Any] = [
        _sanitize(item, max_str, max_keys, max_items, depth + 1)
        for item in items[:max_items]
    ]
    if truncated:
        result.append(f"...and {len(items) - max_items} more items")
    return result


def safe_log_dict(**kwargs: Any) -> dict[str, Any]:
    """Sanitise ``kwargs`` into a dict suitable for ``logger.X(..., extra=...)``.

    Args:
        **kwargs: Caller-supplied log fields.

    Returns:
        Sanitised dict ready for the logging machinery.
    """
    return sanitize_for_log(kwargs)


def truncate_for_log(value: Any, max_length: int = 100) -> str:
    """Truncate a single string for inline logging with a length marker.

    Args:
        value: Source value (stringified if not already a string).
        max_length: Length above which middle-trim kicks in.

    Returns:
        The original string when short enough, otherwise a middle-
        trimmed copy ending in the original length.
    """
    text = value if isinstance(value, str) else str(value)
    if len(text) <= max_length:
        return text
    half = (max_length - 10) // 2
    return f"{text[:half]}...{text[-half:]} ({len(text)} chars)"
