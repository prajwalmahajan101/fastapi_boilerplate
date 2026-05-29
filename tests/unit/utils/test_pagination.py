"""Exemplar unit test — pure helper, no I/O, no fixtures beyond stdlib.

Copy this shape for any new unit test that exercises a single helper
or class in isolation. The points worth lifting:

* Test names spell out the *property* being asserted (not just the
  function name) — they read as a spec in pytest output.
* Each test pins exactly one observable behaviour. Three small tests
  beat one big one when triaging a regression.
* No fixtures, no monkeypatch, no async — unit tests stay this thin.

The helper under test is :class:`src.core.utils.pagination.PageParams`.
"""

from __future__ import annotations

from src.core.utils.pagination import (
    DEFAULT_PAGE,
    DEFAULT_SIZE,
    PageParams,
)


def test_default_page_starts_at_offset_zero() -> None:
    """Page 1 with the default size starts at SQL ``OFFSET 0``."""
    params = PageParams(page=DEFAULT_PAGE, size=DEFAULT_SIZE)
    assert params.offset == 0


def test_offset_advances_by_size_per_page() -> None:
    """Each subsequent page advances ``offset`` by exactly ``size``."""
    page_one = PageParams(page=1, size=25)
    page_three = PageParams(page=3, size=25)
    assert page_three.offset - page_one.offset == 50


def test_limit_is_alias_for_size() -> None:
    """``limit`` is the SQL alias for ``size`` — no off-by-one rewrite."""
    params = PageParams(page=4, size=37)
    assert params.limit == params.size == 37
