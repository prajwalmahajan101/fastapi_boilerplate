"""Unit tests for ``src.core.utils.filters``."""

from __future__ import annotations

import pytest

from src.core.exceptions.validation import ValidationError
from src.core.utils.filters import FilterParam, extract_filters


def test_missing_param_is_skipped() -> None:
    params = [FilterParam("active_only", coerce=bool)]
    assert extract_filters({}, params) == {}


def test_empty_string_is_skipped() -> None:
    params = [FilterParam("active_only", coerce=bool)]
    assert extract_filters({"active_only": ""}, params) == {}


def test_string_default_passes_through() -> None:
    params = [FilterParam("name")]
    assert extract_filters({"name": "widget"}, params) == {"name": "widget"}


def test_int_coercion_succeeds() -> None:
    params = [FilterParam("limit", coerce=int)]
    assert extract_filters({"limit": "25"}, params) == {"limit": 25}


def test_int_coercion_failure_raises_validation_error() -> None:
    params = [FilterParam("limit", coerce=int)]
    with pytest.raises(ValidationError) as ei:
        extract_filters({"limit": "abc"}, params)
    assert ei.value.field == "limit"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("1", True),
        ("YES", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_bool_coercion(raw: str, expected: bool) -> None:
    params = [FilterParam("active_only", coerce=bool)]
    assert extract_filters({"active_only": raw}, params) == {"active_only": expected}


def test_bool_coercion_invalid_value_raises() -> None:
    params = [FilterParam("active_only", coerce=bool)]
    with pytest.raises(ValidationError) as ei:
        extract_filters({"active_only": "maybe"}, params)
    assert ei.value.field == "active_only"


def test_orm_field_overrides_query_param_name() -> None:
    params = [FilterParam("min_qty", "quantity_gte", coerce=int)]
    assert extract_filters({"min_qty": "10"}, params) == {"quantity_gte": 10}


def test_unsupported_coerce_type_raises_type_error() -> None:
    params = [FilterParam("foo", coerce=float)]
    with pytest.raises(TypeError):
        extract_filters({"foo": "1.5"}, params)
