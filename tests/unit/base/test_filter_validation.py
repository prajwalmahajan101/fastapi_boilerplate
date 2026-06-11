"""Unit coverage for ``BaseService._validate_filter_keys``.

The filter-key whitelist is the only piece of ``BaseService`` that runs
without database I/O — perfect candidate for a unit test. It guards
``list`` / ``list_paginated`` / ``filter`` / ``exists`` / ``count``
against attribute leakage when a route lets clients drive arbitrary
filters.

Two strands:

* When ``allowed_filter_fields`` is ``None`` the gate is a no-op (the
  bypass exists so legacy services can opt in incrementally).
* When it is set, every base key — including the ``field__operator``
  shape — must be a member, otherwise a :class:`ValidationError` is
  raised with the documented ``details`` payload.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base.service import BaseService
from src.core.exceptions.validation import ValidationError
from src.model.item import Item


class _OpenService(BaseService[Item]):
    """No whitelist — every key passes through."""

    model = Item


class _RestrictedService(BaseService[Item]):
    """Whitelist set; only ``name`` / ``code`` may appear as filters."""

    model = Item
    allowed_filter_fields = frozenset({"name", "code"})


def _service(cls: type[BaseService[Item]]) -> BaseService[Item]:
    """Build a service with a dummy session — these tests never touch I/O."""
    return cls(session=AsyncSession.__new__(AsyncSession))  # type: ignore[arg-type]


def test_open_service_accepts_arbitrary_keys() -> None:
    """No whitelist means anything goes; the gate must not raise."""
    _service(_OpenService)._validate_filter_keys({"anything": 1, "weird__op": 2})


def test_restricted_service_accepts_whitelisted_keys() -> None:
    """Plain keys that match the whitelist pass."""
    _service(_RestrictedService)._validate_filter_keys({"name": "x", "code": "y"})


def test_restricted_service_strips_operator_suffix() -> None:
    """``field__operator`` keys are validated against the bare ``field`` part."""
    # ``name__icontains`` is accepted because ``name`` is whitelisted.
    _service(_RestrictedService)._validate_filter_keys({"name__icontains": "x"})


def test_restricted_service_rejects_unlisted_key() -> None:
    """A key outside the whitelist raises with the documented details."""
    service = _service(_RestrictedService)
    with pytest.raises(ValidationError) as excinfo:
        service._validate_filter_keys({"secret_column": 1})
    err = excinfo.value
    assert "secret_column" in str(err)
    assert err.details == {"allowed": sorted({"name", "code"})}


def test_restricted_service_rejects_operator_on_unlisted_key() -> None:
    """Sneaking through with ``unlisted__op`` is still rejected on base key."""
    service = _service(_RestrictedService)
    with pytest.raises(ValidationError):
        service._validate_filter_keys({"is_active__icontains": True})


def test_empty_filters_is_a_noop() -> None:
    """An empty dict short-circuits and never raises."""
    _service(_RestrictedService)._validate_filter_keys({})
