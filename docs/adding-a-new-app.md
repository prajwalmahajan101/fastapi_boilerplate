# Adding a new resource

End-to-end checklist for adding a new domain resource — say `Order`
— so it ships with persistence, RBAC, OpenAPI, and tests.

## 1 · Model

`src/model/order.py`:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.base.model import BaseModel


class Order(BaseModel):
    __tablename__ = "orders"

    reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # ... more columns
```

Re-export in `src/model/__init__.py`. Generate the migration:

```bash
alembic revision --autogenerate -m "add orders table"
alembic upgrade head
```

Update [`docs/erd.md`](erd.md) in the same commit.

## 2 · Schema

`src/schema/order.py` — three shapes, no ORM imports:

```python
from pydantic import Field
from src.core.base.schema import BaseSchema


class OrderCreate(BaseSchema):
    reference: str = Field(..., min_length=1, max_length=64)


class OrderUpdate(BaseSchema):
    reference: str | None = None


class OrderRead(BaseSchema):
    id: int
    reference: str
    is_active: bool
```

## 3 · Repository

`src/repository/order.py`:

```python
from src.core.base.repository import BaseRepository
from src.model.order import Order


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_by_reference(self, ref: str) -> Order | None:
        from sqlalchemy import select
        stmt = select(Order).where(Order.reference == ref).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()
```

## 4 · Service

`src/service/order.py`:

```python
from src.core.base.service import BaseService
from src.model.order import Order
from src.repository.order import OrderRepository


class OrderService(BaseService[Order]):
    model = Order
    repository_cls = OrderRepository
    # add business methods here
```

## 5 · RBAC

Add the resource + actions you need in `src/common/enums.py`:

```python
class Resource(StrEnum):
    ...
    ORDER = "order"
```

The action list is shared (`Action.CREATE / READ / UPDATE / DELETE`).
Permission rows are seeded via a management command or migration.

## 6 · Route

`src/api/v1/orders.py`:

```python
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.enums import Action, Resource
from src.common.openapi_metadata import (
    DEFAULT_RESPONSES, RESPONSES_FORBIDDEN, RESPONSES_NOT_FOUND,
    RESPONSES_UNAUTHORIZED,
)
from src.core.api_log import log_inbound_request
from src.core.db.dependencies import get_session
from src.core.db.transaction import atomic
from src.core.rbac import RequireResource
from src.core.resilience.throttle import rate_limit
from src.core.responses import SuccessEnvelope, SuccessResponse
from src.repository.order import OrderRepository
from src.schema.order import OrderCreate, OrderRead
from src.service.order import OrderService

router = APIRouter()
_RES = {**DEFAULT_RESPONSES, **RESPONSES_UNAUTHORIZED, **RESPONSES_FORBIDDEN}


@router.post(
    "/",
    summary="Create an order",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[OrderRead],
    dependencies=[Depends(rate_limit("endpoint", "60/min"))],
    responses=_RES,
)
@log_inbound_request(service_name="orders_api")
async def create_order(
    request: Request,
    payload: OrderCreate,
    user=Depends(RequireResource(Resource.ORDER, Action.CREATE)),
    session: AsyncSession = Depends(get_session),
):
    """Create a new order owned by the calling user."""
    service = OrderService(session)
    async with atomic(session):
        order = await service.create_for_user(user=user, reference=payload.reference)
    return SuccessResponse(
        data=OrderRead.model_validate(order).model_dump(),
        status_code=status.HTTP_201_CREATED,
    )
```

Mount in `src/api/v1/__init__.py`:

```python
v1_router.include_router(orders_router, prefix="/orders", tags=["Orders"])
```

## 7 · Tests

Add the matching unit + integration tests (see
[`testing.md`](testing.md)):

- `tests/unit/service/test_order_service.py` — pure service logic.
- `tests/integration/repository/test_order_repository.py` — real Postgres.
- `tests/e2e/test_orders.py` — full HTTP path.

## 8 · Pre-commit

```bash
pre-commit run --all-files
```

The hooks catch:

- Missing `DEFAULT_RESPONSES` on the new route.
- New public symbol in `src.core` with no caller (only if you
  touched core — domain modules are exempt).
- Doc references the symbol existed but no longer matches.
- `docs/environment.md` drift if you added a new setting.

## 9 · Commit

One atomic commit per logical bucket — feature, refactor, tests,
docs — per the repo-wide git rules in the root `CLAUDE.md`. Don't
mix.
