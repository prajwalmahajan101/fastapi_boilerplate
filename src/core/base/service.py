"""``BaseService`` — async business-logic CRUD with pre/post hooks.

Faithful port of the Django ``BaseService`` shape: same hook surface,
same soft-delete cascade contract, same filter-key whitelist. Write
methods do **not** open their own transaction — they issue
``repository.add`` / ``session.execute`` / ``session.flush`` on the
injected session and rely on the caller to wrap the unit of work in
``async with atomic(session):`` (see ``src/core/db/transaction.py`` —
explicit commit / rollback that tolerates the autobegun transaction
left by the shared-session auth dependency's ``SELECT``). Multiple
writes in one unit of work are therefore atomic by default when the
caller drives a single ``atomic`` block at the route boundary.

The ``user`` parameter is plumbed through every write method and hook
even though auth is not wired yet — callers pass ``None``. When auth
lands and ``BaseModel`` gains the ``AuditedMixin`` audit columns, the
hooks can populate ``created_by_id`` / ``updated_by_id`` without changes
to existing call sites.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Generic, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import RelationshipProperty

from src.core.base.model import BaseModel, NamedBaseModel
from src.core.base.repository import BaseRepository
from src.core.exceptions.repository import EntityNotFoundError
from src.core.exceptions.validation import ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)
NamedModelT = TypeVar("NamedModelT", bound=NamedBaseModel)


class BaseService(ABC, Generic[ModelT]):
    """Generic async service for a single model.

    Subclass::

        class ProductService(BaseService[Product]):
            model = Product

            async def pre_create(self, data, user=None):
                data["sku"] = generate_sku(data["name"])
                return data
    """

    model: type[ModelT]
    repository_cls: type[BaseRepository[ModelT]] = BaseRepository
    allowed_filter_fields: frozenset[str] | None = None

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to a session and instantiate its repository.

        Args:
            session: Active SQLAlchemy ``AsyncSession``.
        """
        self.session = session
        self.repository = self.repository_cls(session, model=self.model)

    # ── Hooks (override in subclasses) ─────────────────────────────────

    async def pre_create(
        self, data: dict[str, Any], user: Any | None = None
    ) -> dict[str, Any]:
        """Mutate or validate ``data`` before insertion. Override in subclasses.

        Args:
            data: Field values for the new instance.
            user: Acting user (optional; auth not yet wired).

        Returns:
            The (possibly modified) data dict to be used for creation.
        """
        return data

    async def post_create(self, instance: ModelT, user: Any | None = None) -> None:
        """Side effects after a successful create. Override in subclasses.

        Args:
            instance: The newly persisted instance.
            user: Acting user (optional).
        """

    async def pre_update(
        self, instance: ModelT, data: dict[str, Any], user: Any | None = None
    ) -> dict[str, Any]:
        """Mutate or validate ``data`` before update. Override in subclasses.

        Args:
            instance: The locked instance about to be updated.
            data: Field values to apply.
            user: Acting user (optional).

        Returns:
            The (possibly modified) data dict to be applied.
        """
        return data

    async def post_update(self, instance: ModelT, user: Any | None = None) -> None:
        """Side effects after a successful update. Override in subclasses.

        Args:
            instance: The refreshed, updated instance.
            user: Acting user (optional).
        """

    async def pre_delete(self, instance: ModelT) -> None:
        """Side effects before a delete. Override in subclasses.

        Args:
            instance: The locked instance about to be (soft-)deleted.
        """

    async def post_delete(self, instance: ModelT, user: Any | None = None) -> None:
        """Side effects after a delete. Override in subclasses.

        Args:
            instance: The deleted (or soft-deleted) instance.
            user: Acting user (optional).
        """

    # ── Filter whitelist ───────────────────────────────────────────────

    def _validate_filter_keys(self, filters: dict[str, Any]) -> None:
        if self.allowed_filter_fields is None:
            return
        for key in filters:
            base_field = key.split("__")[0]
            if base_field not in self.allowed_filter_fields:
                raise ValidationError(
                    f"Filter on '{key}' is not allowed.",
                    details={"allowed": sorted(self.allowed_filter_fields)},
                )

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
            options: SQLAlchemy loader options.

        Returns:
            The matching instance, or ``None`` if not found.
        """
        return await self.repository.get_by_id(pk, options=options)

    async def get_by_id_or_fail(
        self,
        pk: int,
        *,
        options: Sequence[Any] | None = None,
    ) -> ModelT:
        """Like ``get_by_id`` but raises ``EntityNotFoundError`` if missing.

        Args:
            pk: Primary key value.
            options: SQLAlchemy loader options.

        Returns:
            The matching instance.

        Raises:
            EntityNotFoundError: If no row matches ``pk``.
        """
        instance = await self.repository.get_by_id(pk, options=options)
        if instance is None:
            raise EntityNotFoundError(self.model.__name__, pk)
        return instance

    async def get_active_by_id(
        self,
        pk: int,
        *,
        options: Sequence[Any] | None = None,
    ) -> ModelT | None:
        """Fetch a row by primary key restricted to ``is_active=True``.

        Args:
            pk: Primary key value.
            options: SQLAlchemy loader options.

        Returns:
            The matching active instance, or ``None``.
        """
        return await self.repository.get_active_by_id(pk, options=options)

    async def get_active_by_id_or_fail(
        self,
        pk: int,
        *,
        options: Sequence[Any] | None = None,
    ) -> ModelT:
        """Like ``get_active_by_id`` but raises if missing.

        Args:
            pk: Primary key value.
            options: SQLAlchemy loader options.

        Returns:
            The matching active instance.

        Raises:
            EntityNotFoundError: If no active row matches.
        """
        instance = await self.repository.get_active_by_id(pk, options=options)
        if instance is None:
            raise EntityNotFoundError(self.model.__name__, pk)
        return instance

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
        """List rows after validating any filter keys against the whitelist.

        Args:
            filters: Equality filters.
            order_by: SQLAlchemy order clauses.
            limit: Max rows.
            offset: Rows to skip.
            options: Loader options.
            active_only: Restrict to ``is_active=True``.

        Returns:
            Matching model instances.
        """
        if filters:
            self._validate_filter_keys(filters)
        return await self.repository.list(
            filters=filters,
            order_by=order_by,
            limit=limit,
            offset=offset,
            options=options,
            active_only=active_only,
        )

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
        """Paginated ``list`` with the same filter-whitelist contract.

        Args:
            page: 1-indexed page number.
            size: Page size.
            filters: Equality filters (validated against
                ``allowed_filter_fields`` when set).
            order_by: SQLAlchemy order clauses.
            options: Loader options.
            active_only: Restrict to ``is_active=True``.

        Returns:
            Tuple ``(items, total_count)`` — same contract as
            :meth:`BaseRepository.list_paginated`.
        """
        if filters:
            self._validate_filter_keys(filters)
        return await self.repository.list_paginated(
            page=page,
            size=size,
            filters=filters,
            order_by=order_by,
            options=options,
            active_only=active_only,
        )

    async def list_active(self, **kwargs: Any) -> Sequence[ModelT]:
        """List only active rows; shorthand for ``list(active_only=True, ...)``.

        Args:
            **kwargs: Forwarded to ``list``.

        Returns:
            Matching active model instances.
        """
        return await self.list(active_only=True, **kwargs)

    async def filter(self, **kwargs: Any) -> Sequence[ModelT]:
        """Filter rows by equality kwargs after whitelist validation.

        Args:
            **kwargs: Equality filters.

        Returns:
            Matching model instances.
        """
        self._validate_filter_keys(kwargs)
        return await self.repository.filter(**kwargs)

    async def exists(self, **kwargs: Any) -> bool:
        """Return whether any row matches the (whitelisted) filters.

        Args:
            **kwargs: Equality filters.

        Returns:
            ``True`` if at least one row matches.
        """
        self._validate_filter_keys(kwargs)
        return await self.repository.exists(**kwargs)

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count rows matching (whitelisted) filters.

        Args:
            filters: Optional equality filters.

        Returns:
            Number of matching rows.
        """
        if filters:
            self._validate_filter_keys(filters)
        return await self.repository.count(filters)

    # ── Writes ─────────────────────────────────────────────────────────

    async def create(self, data: dict[str, Any], user: Any | None = None) -> ModelT:
        """Create a new instance, invoking ``pre_create`` and ``post_create``.

        Args:
            data: Field values for the new instance.
            user: Acting user (optional).

        Returns:
            The persisted, refreshed instance.

        Raises:
            ValidationError: If a database integrity constraint is violated.
        """
        data = await self.pre_create(data, user)
        instance = self.model(**data)
        try:
            instance = await self.repository.add(instance)
        except IntegrityError as exc:
            raise ValidationError(
                "Integrity constraint violated while creating instance.",
                details={"model": self.model.__name__, "error": str(exc.orig)},
            ) from exc
        await self.post_create(instance, user)
        return instance

    async def bulk_create(
        self,
        data_list: list[dict[str, Any]],
        user: Any | None = None,
    ) -> list[ModelT]:
        """Insert many instances in one flush, skipping per-row hooks.

        Args:
            data_list: List of field-value dicts.
            user: Acting user (optional).

        Returns:
            The persisted, refreshed instances.

        Raises:
            ValidationError: If a database integrity constraint is violated.
        """
        instances = [self.model(**data) for data in data_list]
        try:
            return await self.repository.add_all(instances)
        except IntegrityError as exc:
            raise ValidationError(
                "Integrity constraint violated during bulk_create.",
                details={"model": self.model.__name__, "error": str(exc.orig)},
            ) from exc

    async def update(
        self,
        pk: int,
        data: dict[str, Any],
        *,
        user: Any | None = None,
        active_only: bool = True,
    ) -> ModelT | None:
        """Update a row identified by ``pk`` under a row lock with hooks.

        Args:
            pk: Primary key of the row to update.
            data: Field values to apply.
            user: Acting user (optional).
            active_only: Restrict to ``is_active=True``.

        Returns:
            The refreshed instance, or ``None`` if no row matched.

        Raises:
            ValidationError: If a database integrity constraint is violated.
        """
        # Fetch instance under a row lock so pre_update sees a fresh snapshot.
        from sqlalchemy import select

        stmt = select(self.model).where(self.model.id == pk).with_for_update()
        if active_only:
            stmt = stmt.where(self.model.is_active.is_(True))
        instance = (await self.session.execute(stmt)).scalar_one_or_none()
        if instance is None:
            return None

        data = await self.pre_update(instance, data, user)
        for field, value in data.items():
            setattr(instance, field, value)
        try:
            await self.session.flush()
            await self.session.refresh(instance)
        except IntegrityError as exc:
            raise ValidationError(
                "Integrity constraint violated while updating instance.",
                details={
                    "model": self.model.__name__,
                    "pk": pk,
                    "error": str(exc.orig),
                },
            ) from exc
        await self.post_update(instance, user)
        return instance

    async def update_or_fail(
        self,
        pk: int,
        data: dict[str, Any],
        *,
        user: Any | None = None,
        active_only: bool = True,
    ) -> ModelT:
        """Like ``update`` but raises ``EntityNotFoundError`` if missing.

        Args:
            pk: Primary key of the row to update.
            data: Field values to apply.
            user: Acting user (optional).
            active_only: Restrict to ``is_active=True``.

        Returns:
            The refreshed instance.

        Raises:
            EntityNotFoundError: If no row matches.
        """
        instance = await self.update(pk, data, user=user, active_only=active_only)
        if instance is None:
            raise EntityNotFoundError(self.model.__name__, pk)
        return instance

    async def delete(
        self,
        pk: int,
        *,
        soft: bool = True,
        active_only: bool = True,
        cascade_soft_delete: bool = True,
        user: Any | None = None,
    ) -> bool:
        """Delete (soft by default) the row identified by ``pk``.

        Args:
            pk: Primary key of the row.
            soft: If ``True``, flip ``is_active`` instead of physical delete.
            active_only: Only consider currently-active rows.
            cascade_soft_delete: Recurse into ``delete``/``delete-orphan`` rels.
            user: Acting user (optional).

        Returns:
            ``True`` if a row was deleted, ``False`` if no row matched.
        """
        from sqlalchemy import select

        stmt = select(self.model).where(self.model.id == pk).with_for_update()
        if active_only:
            stmt = stmt.where(self.model.is_active.is_(True))
        instance = (await self.session.execute(stmt)).scalar_one_or_none()
        if instance is None:
            return False

        await self.pre_delete(instance)

        if soft and hasattr(instance, "is_active"):
            instance.is_active = False  # type: ignore[attr-defined]
            await self.session.flush()
            if cascade_soft_delete:
                await self._cascade_soft_delete(instance, user=user)
        else:
            await self.session.delete(instance)
            await self.session.flush()

        await self.post_delete(instance, user=user)
        return True

    async def _cascade_soft_delete(
        self,
        instance: ModelT,
        user: Any | None = None,
    ) -> None:
        """Soft-delete cascaded children that mirror the parent's ``is_active`` flag.

        Walks the SQLAlchemy relationship metadata for ``cascade="all, delete"``
        or ``delete-orphan`` links, filters children to ``is_active=True``,
        flips them off, and recurses. (When auth lands and an ``AuditedMixin``
        adds ``updated_by_id``, propagate ``user`` here too — already plumbed.)

        Args:
            instance: The parent whose children should be soft-deleted.
            user: Acting user (optional).
        """
        mapper = instance.__class__.__mapper__
        for rel in mapper.relationships:
            if not _has_delete_cascade(rel):
                continue
            related_model = rel.mapper.class_
            if not hasattr(related_model, "is_active"):
                continue
            children = await self._collect_active_children(instance, rel.key)
            for child in children:
                child.is_active = False
                await self.session.flush()
                await self._cascade_soft_delete(child, user=user)

    async def _collect_active_children(
        self,
        parent: ModelT,
        relationship_key: str,
    ) -> list[Any]:
        related = await getattr(parent.awaitable_attrs, relationship_key)
        if related is None:
            return []
        if isinstance(related, list):
            return [c for c in related if getattr(c, "is_active", True)]
        return [related] if getattr(related, "is_active", True) else []


def _has_delete_cascade(rel: RelationshipProperty) -> bool:
    cascade = getattr(rel, "cascade", None)
    if cascade is None:
        return False
    return "delete" in cascade or "delete-orphan" in cascade


class BaseNamedModelService(BaseService[NamedModelT], Generic[NamedModelT]):
    """``BaseService`` specialisation for models that extend ``NamedBaseModel``.

    Adds lookup helpers for the unique business ``code`` column that every
    ``NamedBaseModel`` carries — so concrete services over named entities
    don't need a bespoke repository subclass just to expose ``get_by_code``.
    """

    async def get_by_code(self, code: str) -> NamedModelT | None:
        """Return the row matching the unique business ``code``.

        Args:
            code: Unique business identifier (e.g. ``credit_engine``).

        Returns:
            The matching row, or ``None`` if no match.
        """
        stmt = select(self.model).where(self.model.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_code_or_fail(self, code: str) -> NamedModelT:
        """Like ``get_by_code`` but raises ``EntityNotFoundError`` if missing.

        Args:
            code: Unique business identifier.

        Returns:
            The matching row.

        Raises:
            EntityNotFoundError: If no row matches ``code``.
        """
        instance = await self.get_by_code(code)
        if instance is None:
            raise EntityNotFoundError(self.model.__name__, code)
        return instance
