"""Unit tests for ``src.core.utils.data``."""

from __future__ import annotations

import pytest

from src.core.exceptions.validation import ValidationError
from src.core.utils.data import filter_dict_keys, parse_bool, sanitize_string


def test_filter_dict_keys_no_keys_returns_input_unchanged() -> None:
    rows = [{"a": 1, "b": 2}]
    assert filter_dict_keys(rows, []) == rows


def test_filter_dict_keys_loose_drops_missing_silently() -> None:
    rows = [{"a": 1}, {"a": 2, "b": 3}]
    assert filter_dict_keys(rows, ["a", "b"]) == [{"a": 1}, {"a": 2, "b": 3}]


def test_filter_dict_keys_strict_raises_on_missing() -> None:
    rows = [{"a": 1}]
    with pytest.raises(KeyError):
        filter_dict_keys(rows, ["a", "b"], strict=True)


def test_sanitize_string_within_limit() -> None:
    assert sanitize_string("hello", max_length=10) == "hello"


def test_sanitize_string_above_limit_raises() -> None:
    with pytest.raises(ValidationError):
        sanitize_string("a" * 11, max_length=10)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("maybe", False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_parse_bool(value: object, expected: bool) -> None:
    assert parse_bool(value) is expected
