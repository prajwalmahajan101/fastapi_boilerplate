# Class diagrams

> Thin starter doc — extend it as you add base classes or exception families.

## Data-layer base classes

```mermaid
classDiagram
    class BaseModel {
        +int id
        +datetime created_at
        +datetime updated_at
        +bool is_active
        +dict notes
    }
    class NamedBaseModel {
        +str name
        +str code
    }
    class BaseRepository~ModelT~ {
        +get_by_id()
        +list()
        +list_paginated()
        +add()
        +update()
        +delete_hard()
    }
    class BaseService~ModelT~ {
        +create()
        +update()
        +delete()
        +pre_create() / post_create()
        +pre_update() / post_update()
    }
    class BaseNamedModelService~NamedModelT~ {
        +get_by_code()
        +get_by_code_or_fail()
    }

    BaseModel <|-- NamedBaseModel
    BaseService <|-- BaseNamedModelService
    BaseService --> BaseRepository : uses
    BaseRepository --> BaseModel : operates on
```

A concrete resource wires these together: `Item(NamedBaseModel)`,
`ItemRepository(BaseRepository[Item])`,
`ItemService(BaseNamedModelService[Item])`.

## Exception hierarchy

Every custom error derives from `BaseCustomError` and is mapped to an HTTP
status in `core/exceptions/handlers.py` via `register_exception_mapping`
(specific subclass registered before its parent).

```mermaid
classDiagram
    class BaseCustomError
    class APIError
    class ValidationError
    class RepositoryError
    class EntityNotFoundError
    class InfrastructureError
    class ServiceUnavailableError
    class ExternalServiceError
    class TransientError
    class ExternalTimeoutError
    class S3Error
    class SESError
    class UpstreamPushError
    class DecryptionError

    BaseCustomError <|-- APIError
    BaseCustomError <|-- ValidationError
    BaseCustomError <|-- RepositoryError
    RepositoryError <|-- EntityNotFoundError
    BaseCustomError <|-- InfrastructureError
    InfrastructureError <|-- ServiceUnavailableError
    InfrastructureError <|-- ExternalServiceError
    InfrastructureError <|-- DecryptionError
    ExternalServiceError <|-- TransientError
    ExternalServiceError <|-- ExternalTimeoutError
    ExternalServiceError <|-- S3Error
    ExternalServiceError <|-- SESError
    ExternalServiceError <|-- UpstreamPushError
```

### Adding your own family

```python
from src.core.base.exception import BaseCustomError
from src.core.exceptions import register_exception_mapping
from fastapi import status

class PaymentDeclinedError(BaseCustomError):
    default_message = "Payment was declined."
    error_code = "PAYMENT_DECLINED"
    status_code = 402

# register once at startup (specific subclasses before their parent)
register_exception_mapping(PaymentDeclinedError, status.HTTP_402_PAYMENT_REQUIRED)
```

(`UpstreamPushError` ships as a generic "push to an upstream API failed"
example — rename or remove it for your domain.)

## API audit log

`src.core.api_log` is split into focused modules. The two public
decorators are thin: per-direction setup, a `build_log` closure, then
delegation to the shared `capture_and_dispatch` skeleton in
`dispatch.py`. Pure helpers live alongside in `sanitizers.py` /
`error_messages.py`. `decorators.py` is a re-export shim that keeps
the historical import path stable.

```mermaid
classDiagram
    class log_inbound_request {
        +service_name: str
        +decorator(func)
    }
    class log_outbound_request {
        +service_name: str
        +decorator(func)
    }
    class capture_and_dispatch {
        +func, args, kwargs, build_log
        +returns awaitable
    }
    class CaptureState {
        +result: Any
        +exc: Exception | None
        +elapsed_ms: float
        +extras: dict
    }
    class FireAndForgetQueue {
        +max_pending: int
        +submit(coro)
        +drain()
    }
    class persist_log {
        +log: ApiLog
        +never raises
    }
    class sanitizers {
        +redact_headers()
        +truncate()
        +serialize_body()
        +audit_safe()
        +compute_ttl()
    }
    class build_error_message {
        +exc: Exception
        +returns pipe-delimited str
    }
    class ApiLogRepository {
        +save(log)
    }

    log_inbound_request --> capture_and_dispatch : delegates
    log_outbound_request --> capture_and_dispatch : delegates
    capture_and_dispatch --> CaptureState : populates
    capture_and_dispatch --> FireAndForgetQueue : submits via fire_and_forget
    FireAndForgetQueue --> persist_log : drains
    persist_log --> ApiLogRepository : save
    log_inbound_request ..> sanitizers : redact / truncate
    log_outbound_request ..> sanitizers : redact / truncate / audit_safe
    log_inbound_request ..> build_error_message : on exception
    log_outbound_request ..> build_error_message : on exception
```
