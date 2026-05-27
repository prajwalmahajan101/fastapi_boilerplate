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
