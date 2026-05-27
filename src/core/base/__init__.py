"""Public surface of ``core.base`` — base model, service, repository, schema.

``ResponseEnvelope`` and the envelope subclasses live under
``src.core.responses`` (the envelope domain is owned by that package).
Import them from there.
"""

from src.core.base.exception import BaseCustomError
from src.core.base.fields import EncryptedString
from src.core.base.model import BaseModel, NamedBaseModel
from src.core.base.repository import BaseRepository
from src.core.base.schema import BaseSchema
from src.core.base.service import BaseNamedModelService, BaseService

__all__ = [
    "BaseCustomError",
    "BaseModel",
    "BaseNamedModelService",
    "BaseRepository",
    "BaseSchema",
    "BaseService",
    "EncryptedString",
    "NamedBaseModel",
]
