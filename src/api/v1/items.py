"""Items CRUD — example of the full route → service → repository flow.

Each write wraps its unit of work in ``async with atomic(session):`` (the
transaction boundary lives at the route, not the service). Reads stay
outside an explicit transaction. Responses always go through the envelope
factories. Replace this module with your own resources.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.openapi_metadata import DEFAULT_RESPONSES, RESPONSES_NOT_FOUND
from src.core.api_log import log_inbound_request
from src.core.db.dependencies import get_session
from src.core.db.transaction import atomic
from src.core.resilience.throttle import rate_limit
from src.core.responses import (
    PaginatedResponse,
    SuccessEnvelope,
    SuccessResponse,
)
from src.core.responses.schemas import PaginatedData
from src.schema.item import ItemCreate, ItemRead, ItemUpdate
from src.service.item_service import ItemService

router = APIRouter()


@router.post(
    "",
    summary="Create an item",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[ItemRead],
    dependencies=[Depends(rate_limit("endpoint", "30/min"))],
    responses={**DEFAULT_RESPONSES},
)
@log_inbound_request(service_name="example_api")
async def create_item(
    request: Request,
    payload: ItemCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new item.

    Args:
        request: Incoming request (read by the audit decorator).
        payload: Validated create body.
        session: Request-scoped async session.

    Returns:
        201 success envelope carrying the created :class:`ItemRead`.
    """
    service = ItemService(session)
    async with atomic(session):
        item = await service.create(payload.model_dump())
    return SuccessResponse(
        data=ItemRead.model_validate(item).model_dump(),
        message="Item created.",
        status_code=status.HTTP_201_CREATED,
    )


@router.get(
    "",
    summary="List items",
    response_model=SuccessEnvelope[PaginatedData[ItemRead]],
    dependencies=[Depends(rate_limit("endpoint", "100/min"))],
    responses={**DEFAULT_RESPONSES},
)
@log_inbound_request(service_name="example_api")
async def list_items(
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Return a paginated list of active items.

    Args:
        request: Incoming request (read by the audit decorator).
        page: 1-indexed page number.
        size: Page size (max 200).
        session: Request-scoped async session.

    Returns:
        Paginated success envelope of :class:`ItemRead` rows.
    """
    service = ItemService(session)
    items, total = await service.list_paginated(page=page, size=size)
    return PaginatedResponse(
        items=[ItemRead.model_validate(i).model_dump() for i in items],
        page=page,
        size=size,
        total_count=total,
    )


@router.get(
    "/{item_id}",
    summary="Get an item",
    response_model=SuccessEnvelope[ItemRead],
    dependencies=[Depends(rate_limit("endpoint", "100/min"))],
    responses={**DEFAULT_RESPONSES, **RESPONSES_NOT_FOUND},
)
@log_inbound_request(service_name="example_api")
async def get_item(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Fetch one item by id.

    Returns a 404 envelope (via ``EntityNotFoundError`` raised in the
    service) when no item matches ``item_id``.

    Args:
        request: Incoming request (read by the audit decorator).
        item_id: Primary key.
        session: Request-scoped async session.

    Returns:
        Success envelope carrying the :class:`ItemRead`.
    """
    service = ItemService(session)
    item = await service.get_by_id_or_fail(item_id)
    return SuccessResponse(data=ItemRead.model_validate(item).model_dump())


@router.patch(
    "/{item_id}",
    summary="Update an item",
    response_model=SuccessEnvelope[ItemRead],
    dependencies=[Depends(rate_limit("endpoint", "30/min"))],
    responses={**DEFAULT_RESPONSES, **RESPONSES_NOT_FOUND},
)
@log_inbound_request(service_name="example_api")
async def update_item(
    request: Request,
    item_id: int,
    payload: ItemUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an item from a partial body (unset fields are ignored).

    Returns a 404 envelope (via ``EntityNotFoundError`` raised in the
    service) when no item matches ``item_id``.

    Args:
        request: Incoming request (read by the audit decorator).
        item_id: Primary key.
        payload: Validated partial-update body (unset fields are ignored).
        session: Request-scoped async session.

    Returns:
        Success envelope carrying the updated :class:`ItemRead`.
    """
    service = ItemService(session)
    async with atomic(session):
        item = await service.update_or_fail(
            item_id, payload.model_dump(exclude_unset=True)
        )
    return SuccessResponse(
        data=ItemRead.model_validate(item).model_dump(),
        message="Item updated.",
    )


@router.delete(
    "/{item_id}",
    summary="Delete an item",
    response_model=SuccessEnvelope[None],
    dependencies=[Depends(rate_limit("endpoint", "30/min"))],
    responses={**DEFAULT_RESPONSES, **RESPONSES_NOT_FOUND},
)
@log_inbound_request(service_name="example_api")
async def delete_item(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Soft-delete an item (flips ``is_active`` to ``False``).

    Args:
        request: Incoming request (read by the audit decorator).
        item_id: Primary key.
        session: Request-scoped async session.

    Returns:
        Success envelope confirming the delete.

    Raises:
        EntityNotFoundError: If no active item matches ``item_id`` (404).
    """
    service = ItemService(session)
    async with atomic(session):
        deleted = await service.delete(item_id)
    if not deleted:
        from src.core.exceptions.repository import EntityNotFoundError

        raise EntityNotFoundError("Item", item_id)
    return SuccessResponse(message="Item deleted.")


__all__ = ["router"]
