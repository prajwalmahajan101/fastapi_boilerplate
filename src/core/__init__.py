"""Public surface of ``src.core`` — re-exports every symbol callers need.

The dependency rule: ``src.core`` must never import from ``src.common``.
Application code reads ``CoreSettings.*`` via ``src.core.runtime`` and
wires its own concrete ``Settings`` (subclass of ``CoreSettings``) into
the runtime at startup with ``core.runtime.configure(settings)``.

Resilience primitives (retry, circuit breaker, rate-limit, SSRF guard,
field crypto) come from the shared ``resilience-kit`` package — see
``docs/MIGRATION-from-boilerplate-embedded.md`` in that repo for the
mapping. Application code may import the kit directly
(``from resilience_kit import resilient``) or through this module's
re-exports below.
"""

from resilience_kit import (
    FernetCipher,
    circuit_breaker,
    resilient,
    retry_on_failure,
)
from resilience_kit.adapters.fastapi import rate_limit
from resilience_kit.ssrf import assert_public_url

from src.core import runtime
from src.core.api_log import (
    ApiLog,
    ApiLogRepository,
    RequestDirection,
    close_repository,
    get_repository,
    init_repository,
    log_inbound_request,
    log_outbound_request,
    outbound_response_meta_ctx,
)
from src.core.base import (
    BaseCustomError,
    BaseModel,
    BaseNamedModelService,
    BaseRepository,
    BaseSchema,
    BaseService,
    EncryptedString,
    NamedBaseModel,
)
from src.core.context import (
    clear_request_context,
    get_request_id,
    request_id_ctx,
    set_request_context,
)
from src.core.exceptions import (
    APIError,
    DecryptionError,
    EntityNotFoundError,
    ExternalServiceError,
    ExternalTimeoutError,
    InfrastructureError,
    RepositoryError,
    S3Error,
    SESError,
    ServiceUnavailableError,
    TransientError,
    UpstreamPushError,
    ValidationError,
    register_exception_handlers,
    register_exception_mapping,
)
from src.core.middleware.metrics_middleware import MetricsMiddleware
from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.core.responses import (
    ErrorDetail,
    ErrorEnvelope,
    ErrorResponse,
    PaginatedData,
    PaginatedResponse,
    ResponseEnvelope,
    SuccessEnvelope,
    SuccessResponse,
)
from src.core.settings import CoreSettings
from src.core.utils.db import (
    dispose_all_engines,
    get_app_engine,
    get_async_engine,
    get_sessionmaker,
)
from src.core.utils.function_logger import log_function
from src.core.utils.log_sanitization import (
    safe_log_dict,
    sanitize_for_log,
    truncate_for_log,
)
from src.core.utils.logging import (
    RequestContextFilter,
    get_logger,
    setup_logging,
)
from src.core.utils.redis import close_all_redis_clients, get_redis_client
from src.core.utils.s3 import (
    AsyncS3Client,
    build_s3_uri,
    generate_object_key,
    parse_s3_uri,
)
from src.core.utils.ses import AsyncSESClient, EmailAttachment, EmailMessage

__all__ = [
    # Audit log (boilerplate-owned HTTP request audit)
    "ApiLog",
    "ApiLogRepository",
    "RequestDirection",
    "close_repository",
    "get_repository",
    "init_repository",
    "log_inbound_request",
    "log_outbound_request",
    "outbound_response_meta_ctx",
    # Base ORM + service / repository scaffolding
    "BaseCustomError",
    "BaseModel",
    "BaseNamedModelService",
    "BaseRepository",
    "BaseSchema",
    "BaseService",
    "EncryptedString",
    "NamedBaseModel",
    # Context vars
    "clear_request_context",
    "get_request_id",
    "request_id_ctx",
    "set_request_context",
    # Exceptions (boilerplate-domain — kit-owned ones come from
    # ``resilience_kit.exceptions`` directly)
    "APIError",
    "DecryptionError",
    "EntityNotFoundError",
    "ExternalServiceError",
    "ExternalTimeoutError",
    "InfrastructureError",
    "RepositoryError",
    "S3Error",
    "SESError",
    "ServiceUnavailableError",
    "TransientError",
    "UpstreamPushError",
    "ValidationError",
    "register_exception_handlers",
    "register_exception_mapping",
    # Middleware (boilerplate-specific only — kit-owned middleware is
    # installed via ``resilience_kit.adapters.fastapi.install_middleware_stack``)
    "MetricsMiddleware",
    "RequestLoggingMiddleware",
    # Resilience primitives (kit-owned; re-exported here for legacy callers
    # — new code should ``from resilience_kit import ...`` directly)
    "FernetCipher",
    "assert_public_url",
    "circuit_breaker",
    "rate_limit",
    "resilient",
    "retry_on_failure",
    # Responses (envelope shapes)
    "ErrorDetail",
    "ErrorEnvelope",
    "ErrorResponse",
    "PaginatedData",
    "PaginatedResponse",
    "ResponseEnvelope",
    "SuccessEnvelope",
    "SuccessResponse",
    # Settings + runtime
    "CoreSettings",
    "runtime",
    # Utils
    "AsyncS3Client",
    "AsyncSESClient",
    "EmailAttachment",
    "EmailMessage",
    "RequestContextFilter",
    "build_s3_uri",
    "close_all_redis_clients",
    "dispose_all_engines",
    "generate_object_key",
    "get_app_engine",
    "get_async_engine",
    "get_logger",
    "get_redis_client",
    "get_sessionmaker",
    "log_function",
    "parse_s3_uri",
    "safe_log_dict",
    "sanitize_for_log",
    "setup_logging",
    "truncate_for_log",
]
