"""Concrete exception hierarchy + FastAPI handler registration.

The families shipped here are domain-independent:

* :mod:`api` — ``APIError`` for failures while calling an upstream HTTP API.
* :mod:`infrastructure` — caches, encryption, external services, timeouts.
* :mod:`repository` — persistence-layer failures (``EntityNotFoundError``).
* :mod:`validation` — ``ValidationError`` for domain validation.

Project code adds its own families by subclassing ``BaseCustomError`` and
calling :func:`register_exception_mapping` to bind a status code (see
``handlers.py`` for the pre-registered mappings and ordering rules).
"""

from src.core.base.exception import BaseCustomError
from src.core.exceptions.api import APIError
from src.core.exceptions.handlers import (
    register_exception_handlers,
    register_exception_mapping,
)
from src.core.exceptions.infrastructure import (
    DecryptionError,
    ExternalServiceError,
    ExternalTimeoutError,
    InfrastructureError,
    S3Error,
    SESError,
    ServiceUnavailableError,
    TransientError,
    UpstreamPushError,
)
from src.core.exceptions.rate_limit import RateLimitError
from src.core.exceptions.repository import (
    EntityNotFoundError,
    RepositoryError,
)
from src.core.exceptions.validation import ValidationError

__all__ = [
    "APIError",
    "BaseCustomError",
    "DecryptionError",
    "EntityNotFoundError",
    "ExternalServiceError",
    "ExternalTimeoutError",
    "InfrastructureError",
    "RateLimitError",
    "RepositoryError",
    "S3Error",
    "SESError",
    "ServiceUnavailableError",
    "TransientError",
    "UpstreamPushError",
    "ValidationError",
    "register_exception_handlers",
    "register_exception_mapping",
]
