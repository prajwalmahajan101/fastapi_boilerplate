"""Reusable query-parameter-to-repository-filter extraction.

FastAPI's ``Query(...)`` covers most validation/coercion needs at the
route layer, but services / repositories that accept a raw ``filters``
mapping benefit from a single, typed conversion helper. Used when a
handler receives the full ``request.query_params`` (a Starlette
``QueryParams`` mapping or any ``Mapping[str, str]``-shaped object)
and wants to forward a typed subset to a repository call.

Example::

    fps = [
        FilterParam("active_only", coerce=bool),
        FilterParam("min_quantity", "quantity_gte", coerce=int),
    ]
    filters = extract_filters(request.query_params, fps)
    items = await item_repo.list(filters=filters)
"""

from __future__ import annotations

from typing import Any, Mapping

from src.core.exceptions.validation import ValidationError

_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


class FilterParam:
    """Declares a single query parameter that maps to a repository filter key.

    Attributes:
        query_param: Name in the URL query string (``?key=value``).
        orm_field: Key passed to the repository ``filters`` mapping;
            defaults to *query_param* when omitted.
        coerce: Target type — ``int``, ``bool``, or ``str`` (default).
    """

    __slots__ = ("query_param", "orm_field", "coerce")

    def __init__(
        self,
        query_param: str,
        orm_field: str | None = None,
        *,
        coerce: type = str,
    ) -> None:
        """Bind the FilterParam to a query-string name + coerce type.

        Args:
            query_param: Name in the URL query string.
            orm_field: Repository filter key; defaults to *query_param*.
            coerce: One of ``int`` / ``bool`` / ``str``. Other types
                raise ``TypeError`` at extract time.
        """
        self.query_param = query_param
        self.orm_field = orm_field or query_param
        self.coerce = coerce


def extract_filters(
    query_params: Mapping[str, str],
    filter_params: list[FilterParam],
) -> dict[str, Any]:
    """Extract and coerce declared query params into a repository filter dict.

    Missing params are silently skipped (not supplied = no filter).
    Empty strings are also treated as not-supplied so consumers can
    pass through a ``?key=`` form without producing a spurious filter.

    Args:
        query_params: A ``Mapping[str, str]`` — Starlette's
            ``request.query_params`` works directly.
        filter_params: Declared :class:`FilterParam` instances.

    Returns:
        A dict mapping repository filter keys to coerced values.

    Raises:
        ValidationError: On type-coercion failure. The exception's
            ``field`` is set to the offending ``query_param`` so the
            error envelope identifies the bad input precisely.
    """
    filters: dict[str, Any] = {}
    for fp in filter_params:
        raw = query_params.get(fp.query_param)
        if raw is None or raw == "":
            continue
        filters[fp.orm_field] = _coerce(raw, fp.coerce, fp.query_param)
    return filters


def _coerce(raw: str, target: type, param_name: str) -> Any:
    """Coerce a raw query-param string to the declared type.

    Args:
        raw: Raw string value from the query string.
        target: Target type — ``int`` / ``bool`` / ``str``.
        param_name: Original query param name (surfaced in the error).

    Returns:
        The coerced value.

    Raises:
        ValidationError: On coercion failure (bad int / bad bool).
        TypeError: If ``target`` is not one of the supported types.
    """
    if target is str:
        return raw
    if target is int:
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"Parameter '{param_name}' must be an integer.",
                field=param_name,
            ) from exc
    if target is bool:
        lower = raw.strip().lower()
        if lower in _BOOL_TRUE:
            return True
        if lower in _BOOL_FALSE:
            return False
        raise ValidationError(
            f"Parameter '{param_name}' must be a boolean (true/false).",
            field=param_name,
        )
    raise TypeError(
        f"FilterParam coerce={target!r} is not supported (use int, bool, or str)."
    )


__all__ = ["FilterParam", "extract_filters"]
