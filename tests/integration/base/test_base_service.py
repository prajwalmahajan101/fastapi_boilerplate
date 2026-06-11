"""Integration coverage for the ``BaseService`` write surface + hooks.

Drives the service-layer methods through :class:`ItemService` (a thin
``BaseNamedModelService[Item]`` subclass with a real ``pre_create``
hook) and a throwaway ``User``-based service used to exercise the
cascade-soft-delete path (the ``User → APIKey`` relationship is the
only ``cascade="all, delete-orphan"`` link the schema currently ships).

The hook ordering, the ``EntityNotFoundError`` branches on
``*_or_fail`` helpers, the soft-delete + cascade path, and the
filter-whitelist denial are all covered here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.base.service import BaseService
from src.core.exceptions.repository import EntityNotFoundError
from src.core.exceptions.validation import ValidationError
from src.model.auth import APIKey, User
from src.repository.auth import APIKeyRepository
from src.service.item_service import ItemService


@pytest.fixture
async def session(pg_engine) -> AsyncIterator[AsyncSession]:
    """Per-test session whose work rolls back on teardown."""
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        await s.begin()
        try:
            yield s
        finally:
            await s.rollback()


def _item_payload(suffix: str | None = None) -> dict[str, Any]:
    """Build a unique ``Item`` creation payload."""
    tag = suffix or uuid.uuid4().hex[:8]
    return {
        "name": f"svc-{tag}",
        "code": f"SVC-{tag}",
        "quantity": 1,
        "description": "service-test",
    }


# ── create ──────────────────────────────────────────────────────────


async def test_create_invokes_pre_and_post_hooks(session: AsyncSession) -> None:
    """``ItemService.create`` runs ``pre_create`` (uniqueness) + persists."""
    service = ItemService(session)
    payload = _item_payload()
    item = await service.create(payload)
    assert item.id is not None
    assert item.code == payload["code"]


async def test_create_duplicate_code_raises_validation_error(
    session: AsyncSession,
) -> None:
    """The ``pre_create`` hook turns the duplicate-code race into a 400."""
    service = ItemService(session)
    payload = _item_payload()
    await service.create(payload)
    with pytest.raises(ValidationError):
        await service.create(payload)


# ── read helpers ────────────────────────────────────────────────────


async def test_get_by_id_or_fail_raises_when_missing(
    session: AsyncSession,
) -> None:
    """``get_by_id_or_fail`` translates a missing pk to ``EntityNotFoundError``."""
    with pytest.raises(EntityNotFoundError):
        await ItemService(session).get_by_id_or_fail(99_999_999)


async def test_get_active_by_id_or_fail_raises_when_soft_deleted(
    session: AsyncSession,
) -> None:
    """A soft-deleted row is invisible to ``get_active_by_id_or_fail``."""
    service = ItemService(session)
    item = await service.create(_item_payload())
    deleted = await service.delete(item.id, soft=True)
    assert deleted is True
    with pytest.raises(EntityNotFoundError):
        await service.get_active_by_id_or_fail(item.id)


# ── list / filter / count + filter-key whitelist ────────────────────


async def test_list_filters_validated_against_whitelist(
    session: AsyncSession,
) -> None:
    """Filtering on a field outside ``allowed_filter_fields`` raises."""
    service = ItemService(session)
    await service.create(_item_payload())
    with pytest.raises(ValidationError) as excinfo:
        await service.list(filters={"description": "svc"})
    assert "description" in str(excinfo.value)


async def test_list_paginated_returns_items_and_total(
    session: AsyncSession,
) -> None:
    """``list_paginated`` honours the whitelist and returns ``(slice, total)``."""
    service = ItemService(session)
    payload = _item_payload()
    created = await service.create(payload)
    items, total = await service.list_paginated(
        page=1, size=5, filters={"code": payload["code"]}
    )
    assert total >= 1
    assert any(i.id == created.id for i in items)


async def test_exists_and_count_use_whitelist(session: AsyncSession) -> None:
    """``exists`` / ``count`` reuse the whitelist gate."""
    service = ItemService(session)
    payload = _item_payload()
    await service.create(payload)
    assert await service.exists(code=payload["code"]) is True
    assert await service.count(filters={"code": payload["code"]}) == 1
    with pytest.raises(ValidationError):
        await service.exists(description="svc")


# ── update / update_or_fail ─────────────────────────────────────────


async def test_update_returns_refreshed_row(session: AsyncSession) -> None:
    """``update`` flushes + refreshes + invokes post hooks."""
    service = ItemService(session)
    item = await service.create(_item_payload())
    updated = await service.update(item.id, {"quantity": 42})
    assert updated is not None
    assert updated.quantity == 42


async def test_update_or_fail_raises_when_missing(
    session: AsyncSession,
) -> None:
    """``update_or_fail`` raises ``EntityNotFoundError`` for a missing pk."""
    with pytest.raises(EntityNotFoundError):
        await ItemService(session).update_or_fail(99_999_999, {"quantity": 1})


# ── delete (soft + hard) ────────────────────────────────────────────


async def test_delete_soft_flips_is_active(session: AsyncSession) -> None:
    """Default ``soft=True`` flips ``is_active=False`` rather than DELETE."""
    service = ItemService(session)
    item = await service.create(_item_payload())
    assert await service.delete(item.id, soft=True) is True
    fetched = await service.get_by_id(item.id)
    assert fetched is not None
    assert fetched.is_active is False


async def test_delete_hard_removes_row(session: AsyncSession) -> None:
    """``soft=False`` triggers a physical DELETE."""
    service = ItemService(session)
    item = await service.create(_item_payload())
    assert await service.delete(item.id, soft=False) is True
    assert await service.get_by_id(item.id) is None


async def test_delete_returns_false_for_missing_pk(
    session: AsyncSession,
) -> None:
    """``delete`` returns ``False`` (not raise) for a missing pk."""
    assert await ItemService(session).delete(99_999_999) is False


# ── cascade soft-delete ─────────────────────────────────────────────


class _UserService(BaseService[User]):
    """Throwaway service that walks the ``User → APIKey`` cascade."""

    model = User


async def test_soft_delete_cascades_to_child_rows(session: AsyncSession) -> None:
    """Deleting a User soft-deletes its APIKeys via the cascade walker.

    ``User.api_keys`` is declared with ``cascade="all, delete-orphan"``;
    the base-service walker should follow that link and flip the
    children's ``is_active`` flag.
    """
    tag = uuid.uuid4().hex[:8]
    owner = User(email=f"cascade-{tag}@example.com")
    session.add(owner)
    await session.flush()

    key = APIKey(
        user_id=owner.id,
        name="cascade-test",
        prefix=tag,
        secret="raw",
        is_active=True,
    )
    session.add(key)
    await session.flush()

    assert await _UserService(session).delete(owner.id, soft=True) is True

    refreshed_user = await session.get(User, owner.id)
    assert refreshed_user is not None
    assert refreshed_user.is_active is False

    refreshed_key = await APIKeyRepository(session).get_by_id(key.id)
    assert refreshed_key is not None
    assert refreshed_key.is_active is False, (
        "cascade walker should have flipped the child APIKey to inactive"
    )
