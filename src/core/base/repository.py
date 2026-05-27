"""``BaseRepository`` — async SQLAlchemy CRUD primitives.

Sits between the ORM and ``BaseService``. Owns the actual ``select`` /
``update`` / ``delete`` statements; ``BaseService`` adds the surrounding
transaction boundary, pre/post hooks, and cascade soft-delete logic.

The ``options`` parameter on read methods accepts SQLAlchemy loader
options (``selectinload`` / ``joinedload``) — the async equivalent of
Django's ``select_related`` / ``prefetch_related``.
"""

from __future__ import annotations

from typing import Any, Generic, Sequence, TypeVar

from sqlalchemy import delete as sqla_delete
from sqlalchemy import func, select, update as sqla_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base.model import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseRepository(Generic[ModelT]):
    """Generic async CRUD over a single SQLAlchemy model."""

    #: Concrete subclass must set this OR pass model via constructor.
    model: type[ModelT]

    def __init__(
        self, session: AsyncSession, model: type[ModelT] | None = None
    ) -> None:
        """Bind the repository to an async session and optionally a model.

        Args:
            session: Active SQLAlchemy ``AsyncSession``.
            model: Concrete model class; required if not set at the class level.
        """
        self.session = session
        if model is not None:
            self.model = model

    # ── Reads ──────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        pk: int,
        *,
        options: Sequence[Any] | None = None,
    ) -> ModelT | None:
        """Fetch a row by primary key, ignoring the soft-delete flag.

        Args:
            pk: Primary key value.
            options: SQLAlchemy loader options (e.g. ``selectinload``).

        Returns:
            The matching instance, or ``None`` if not found.
        """
        stmt = select(self.model).where(self.model.id == pk)
        if options:
            stmt = stmt.options(*options)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_by_id(
        self,
        pk: int,
        *,
        options: Sequence[Any] | None = None,
    ) -> ModelT | None:
        """Fetch a row by primary key, filtered to ``is_active=True``.

        Args:
            pk: Primary key value.
            options: SQLAlchemy loader options.

        Returns:
            The matching active instance, or ``None`` if not found.
        """
        stmt = (
            select(self.model)
            .where(self.model.id == pk)
            .where(self.model.is_active.is_(True))
        )
        if options:
            stmt = stmt.options(*options)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        order_by: Sequence[Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        options: Sequence[Any] | None = None,
        active_only: bool = False,
    ) -> Sequence[ModelT]:
        """List rows with optional filters, ordering, pagination, and loaders.

        Args:
            filters: Equality filters applied via ``filter_by``.
            order_by: SQLAlchemy order clauses.
            limit: Max number of rows.
            offset: Number of rows to skip.
            options: SQLAlchemy loader options.
            active_only: Restrict to ``is_active=True``.

        Returns:
            Sequence of matching model instances.
        """
        stmt = select(self.model)
        if active_only:
            stmt = stmt.where(self.model.is_active.is_(True))
        if filters:
            stmt = stmt.filter_by(**filters)
        if order_by:
            stmt = stmt.order_by(*order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)
        if options:
            stmt = stmt.options(*options)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_paginated(
        self,
        *,
        page: int,
        size: int,
        filters: dict[str, Any] | None = None,
        order_by: Sequence[Any] | None = None,
        options: Sequence[Any] | None = None,
        active_only: bool = False,
    ) -> tuple[Sequence[ModelT], int]:
        """Return ``(items, total_count)`` for the requested page slice.

        Runs a ``COUNT`` query against ``filters`` first so the caller
        knows the full dataset size, then a bounded ``SELECT`` for the
        page itself. Two round-trips per request — the standard
        offset-pagination shape.

        Args:
            page: 1-indexed page number.
            size: Page size (rows per page).
            filters: Equality filters applied to both ``count`` and ``list``.
            order_by: SQLAlchemy order clauses for the slice query.
            options: SQLAlchemy loader options.
            active_only: Restrict to ``is_active=True`` on both queries.

        Returns:
            Tuple ``(items, total_count)`` — ``items`` is the slice for
            this page; ``total_count`` is the full row count behind
            ``filters`` (not just the slice).
        """
        total_count = await self.count(filters, active_only=active_only)
        items = await self.list(
            filters=filters,
            order_by=order_by,
            limit=size,
            offset=(page - 1) * size,
            options=options,
            active_only=active_only,
        )
        return items, total_count

    async def filter(self, **kwargs: Any) -> Sequence[ModelT]:
        """Shorthand for ``select(...).filter_by(**kwargs)``.

        Args:
            **kwargs: Equality filters.

        Returns:
            Sequence of matching model instances.
        """
        stmt = select(self.model).filter_by(**kwargs)
        return (await self.session.execute(stmt)).scalars().all()

    async def exists(self, **kwargs: Any) -> bool:
        """Return whether any row matches ``kwargs``.

        Args:
            **kwargs: Equality filters.

        Returns:
            ``True`` if at least one row matches.
        """
        stmt = select(func.count()).select_from(self.model).filter_by(**kwargs)
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def count(
        self,
        filters: dict[str, Any] | None = None,
        *,
        active_only: bool = False,
    ) -> int:
        """Count rows matching ``filters``.

        Args:
            filters: Optional equality filters.
            active_only: Restrict to ``is_active=True`` so the count
                matches a corresponding ``list(active_only=True)`` call.

        Returns:
            Number of matching rows.
        """
        stmt = select(func.count()).select_from(self.model)
        if active_only:
            stmt = stmt.where(self.model.is_active.is_(True))
        if filters:
            stmt = stmt.filter_by(**filters)
        return (await self.session.execute(stmt)).scalar_one()

    # ── Writes ─────────────────────────────────────────────────────────

    async def add(self, instance: ModelT) -> ModelT:
        """Insert a single instance, flush, and refresh it.

        Args:
            instance: Unsaved model instance.

        Returns:
            The persisted, refreshed instance.
        """
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def add_all(self, instances: list[ModelT]) -> list[ModelT]:
        """Insert several instances, flush, and refresh each.

        Args:
            instances: Unsaved model instances.

        Returns:
            The persisted, refreshed list of instances.
        """
        self.session.add_all(instances)
        await self.session.flush()
        for instance in instances:
            await self.session.refresh(instance)
        return instances

    async def update(
        self,
        pk: int,
        data: dict[str, Any],
        *,
        active_only: bool = True,
        lock: bool = True,
    ) -> ModelT | None:
        """Update the row identified by ``pk`` with ``data``.

        Args:
            pk: Primary key of the row to update.
            data: Field/value pairs to set.
            active_only: Restrict to ``is_active=True``.
            lock: Acquire ``FOR UPDATE`` lock during the read.

        Returns:
            The updated instance, or ``None`` if no row matched.
        """
        stmt = select(self.model).where(self.model.id == pk)
        if active_only:
            stmt = stmt.where(self.model.is_active.is_(True))
        if lock:
            stmt = stmt.with_for_update()

        instance = (await self.session.execute(stmt)).scalar_one_or_none()
        if instance is None:
            return None

        for field, value in data.items():
            setattr(instance, field, value)

        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def bulk_update_by_filter(
        self,
        filters: dict[str, Any],
        values: dict[str, Any],
    ) -> int:
        """Issue a single ``UPDATE ... WHERE filters`` and return row count.

        Args:
            filters: Equality filters identifying rows to update.
            values: Field/value pairs to set.

        Returns:
            Number of affected rows.
        """
        stmt = sqla_update(self.model).filter_by(**filters).values(**values)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def delete_hard(self, instance: ModelT) -> None:
        """Permanently delete an instance from the database.

        Args:
            instance: Persisted model instance to delete.
        """
        await self.session.delete(instance)
        await self.session.flush()

    async def delete_hard_by_id(self, pk: int) -> int:
        """Permanently delete the row identified by ``pk``.

        Args:
            pk: Primary key of the row to delete.

        Returns:
            Number of affected rows (0 or 1).
        """
        stmt = sqla_delete(self.model).where(self.model.id == pk)
        result = await self.session.execute(stmt)
        return result.rowcount or 0
