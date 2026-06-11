"""Integration coverage for the ``BaseRepository`` generic CRUD surface.

Drives the inherited methods through :class:`ItemRepository` against a
real Postgres engine. The repo is generic over ``ModelT`` — every
read/write method exercised here is shared with every future concrete
repository, so this file is the regression net for the boilerplate's
data-access primitives.

Reads — ``get_by_id``, ``get_active_by_id``, ``list``, ``list_paginated``,
``filter``, ``exists``, ``count`` (including the ``active_only`` toggle).
Writes — ``add``, ``add_all``, ``update``, ``bulk_update_by_filter``,
``delete_hard``, ``delete_hard_by_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.model.item import Item
from src.repository.item_repo import ItemRepository


@pytest.fixture
async def session(pg_engine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose work rolls back on teardown."""
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        await s.begin()
        try:
            yield s
        finally:
            await s.rollback()


@pytest.fixture
async def seeded_items(session: AsyncSession) -> list[Item]:
    """Seed five distinct items — three active, two inactive."""
    tag = uuid.uuid4().hex[:8]
    items = [
        Item(
            name=f"alpha-{tag}",
            code=f"A-{tag}",
            quantity=1,
            description="alpha",
            is_active=True,
        ),
        Item(
            name=f"beta-{tag}",
            code=f"B-{tag}",
            quantity=2,
            description="beta",
            is_active=True,
        ),
        Item(
            name=f"gamma-{tag}",
            code=f"C-{tag}",
            quantity=3,
            description="gamma",
            is_active=True,
        ),
        Item(
            name=f"inactive-{tag}",
            code=f"D-{tag}",
            quantity=4,
            description="tombstone",
            is_active=False,
        ),
        Item(
            name=f"also-inactive-{tag}",
            code=f"E-{tag}",
            quantity=5,
            description="tombstone",
            is_active=False,
        ),
    ]
    session.add_all(items)
    await session.flush()
    return items


# ── reads ───────────────────────────────────────────────────────────


async def test_get_by_id_returns_row_regardless_of_active_flag(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``get_by_id`` is intentionally soft-delete agnostic."""
    repo = ItemRepository(session)
    inactive = next(i for i in seeded_items if not i.is_active)
    fetched = await repo.get_by_id(inactive.id)
    assert fetched is not None
    assert fetched.id == inactive.id


async def test_get_by_id_returns_none_for_missing_pk(
    session: AsyncSession,
) -> None:
    """Missing pk returns ``None`` rather than raising."""
    assert await ItemRepository(session).get_by_id(99_999_999) is None


async def test_get_active_by_id_skips_soft_deleted(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``get_active_by_id`` filters out inactive rows."""
    repo = ItemRepository(session)
    inactive = next(i for i in seeded_items if not i.is_active)
    assert await repo.get_active_by_id(inactive.id) is None
    active = next(i for i in seeded_items if i.is_active)
    fetched = await repo.get_active_by_id(active.id)
    assert fetched is not None


async def test_list_active_only_excludes_inactive(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """The default ``active_only=True`` hides tombstones."""
    repo = ItemRepository(session)
    seeded_ids = {i.id for i in seeded_items}
    rows = await repo.list()
    fetched_ids = {row.id for row in rows if row.id in seeded_ids}
    expected = {i.id for i in seeded_items if i.is_active}
    assert fetched_ids == expected


async def test_list_active_false_includes_inactive(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """Explicit ``active_only=False`` returns tombstones too."""
    repo = ItemRepository(session)
    seeded_ids = {i.id for i in seeded_items}
    rows = await repo.list(active_only=False)
    fetched_ids = {row.id for row in rows if row.id in seeded_ids}
    assert fetched_ids == seeded_ids


async def test_list_with_filters_and_pagination_clauses(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``list`` accepts ``filters``, ``order_by``, ``limit``, ``offset``."""
    repo = ItemRepository(session)
    tag = seeded_items[0].code.split("-", 1)[1]
    rows = await repo.list(
        filters={"description": "alpha"},
        order_by=[Item.id.asc()],
        limit=10,
    )
    assert all(r.description == "alpha" for r in rows)
    assert any(r.code == f"A-{tag}" for r in rows)


async def test_list_paginated_returns_items_and_total(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``list_paginated`` returns ``(slice, total)`` honouring filters."""
    del seeded_items  # not directly inspected; fixture populates the table
    repo = ItemRepository(session)
    items_page, total = await repo.list_paginated(
        page=1, size=2, order_by=[Item.id.asc()]
    )
    assert len(items_page) <= 2
    assert total >= len(items_page)


async def test_filter_kwargs_short_hand(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``filter(**kwargs)`` is the equality-only shorthand."""
    target = next(i for i in seeded_items if i.is_active)
    repo = ItemRepository(session)
    rows = await repo.filter(code=target.code)
    assert len(rows) == 1
    assert rows[0].id == target.id


async def test_exists_returns_bool(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``exists`` distinguishes match vs no-match."""
    target = seeded_items[0]
    repo = ItemRepository(session)
    assert await repo.exists(code=target.code) is True
    assert await repo.exists(code="no-such-code-ever-9999") is False


async def test_count_respects_active_only_default(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``count`` defaults to ``active_only=True`` like ``list``."""
    repo = ItemRepository(session)
    active = await repo.count()
    everything = await repo.count(active_only=False)
    assert everything > active


# ── writes ──────────────────────────────────────────────────────────


async def test_add_flushes_and_refreshes(session: AsyncSession) -> None:
    """``add`` persists, flushes, refreshes — id and timestamps populated."""
    tag = uuid.uuid4().hex[:8]
    item = Item(name=f"adder-{tag}", code=f"AD-{tag}", quantity=7)
    persisted = await ItemRepository(session).add(item)
    assert persisted.id is not None
    assert persisted.created_at is not None
    assert persisted.updated_at is not None


async def test_add_all_persists_many(session: AsyncSession) -> None:
    """``add_all`` flushes the batch and refreshes each instance."""
    tag = uuid.uuid4().hex[:8]
    items = [
        Item(name=f"bulk-{tag}-{i}", code=f"BULK-{tag}-{i}", quantity=i)
        for i in range(3)
    ]
    persisted = await ItemRepository(session).add_all(items)
    assert all(p.id is not None for p in persisted)
    assert {p.code for p in persisted} == {f"BULK-{tag}-{i}" for i in range(3)}


async def test_update_returns_refreshed_row(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``update`` applies the diff and returns the refreshed row."""
    target = next(i for i in seeded_items if i.is_active)
    updated = await ItemRepository(session).update(
        target.id, {"quantity": 999, "description": "patched"}
    )
    assert updated is not None
    assert updated.quantity == 999
    assert updated.description == "patched"


async def test_update_returns_none_for_inactive_when_active_only(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """The default ``active_only=True`` refuses to touch tombstones."""
    inactive = next(i for i in seeded_items if not i.is_active)
    assert (await ItemRepository(session).update(inactive.id, {"quantity": 0})) is None


async def test_bulk_update_by_filter_returns_rowcount(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``bulk_update_by_filter`` runs a single UPDATE and returns rowcount."""
    affected = await ItemRepository(session).bulk_update_by_filter(
        {"description": "alpha"}, {"description": "alpha-renamed"}
    )
    assert affected >= 1
    # confirm side-effect landed
    rows = await ItemRepository(session).filter(description="alpha-renamed")
    assert rows
    del seeded_items  # fixture-only


async def test_delete_hard_removes_row(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``delete_hard`` issues a physical DELETE."""
    target = seeded_items[-1]
    repo = ItemRepository(session)
    await repo.delete_hard(target)
    assert await repo.get_by_id(target.id) is None


async def test_delete_hard_by_id_returns_rowcount(
    session: AsyncSession, seeded_items: list[Item]
) -> None:
    """``delete_hard_by_id`` returns 1 on hit, 0 on miss."""
    target = seeded_items[0]
    repo = ItemRepository(session)
    assert await repo.delete_hard_by_id(target.id) == 1
    assert await repo.delete_hard_by_id(99_999_999) == 0
