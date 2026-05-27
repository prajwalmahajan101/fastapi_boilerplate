"""Public surface of ``src.core`` — re-exports every symbol callers need.

The dependency rule: ``src.core`` must never import from ``src.common``.
Application code reads ``CoreSettings.*`` via ``src.core.runtime`` and
wires its own concrete ``Settings`` (subclass of ``CoreSettings``) into
the runtime at startup with ``core.runtime.configure(settings)``.
"""

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
from src.core.lifecycle import (
    HealthCheckResult,
    cache_check,
    create_health_router,
    create_readiness_router,
    db_check,
    throttle_check,
)
from src.core.middleware import (
    ExceptionLoggingMiddleware,
    RateLimitHeadersMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    SelectiveCORSMiddleware,
    install_core_middleware,
)
from src.core.resilience import (
    circuit_breaker,
    resilience_registry,
    resilient,
    retry_on_failure,
    retry_with_exponential_backoff,
)
from src.core.resilience.cache import (
    BaseCacheBackend,
    CacheVersionError,
    bump_dataset_cache_version,
    generate_cache_key,
    get_cache,
    get_cached_result,
    get_dataset_cache_version,
    set_cached_result,
)
from src.core.resilience.circuit_breaker import (
    BaseCircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)
from src.core.resilience.circuit_breaker import (
    get_registry as get_circuit_breaker_registry,
)
from src.core.resilience.throttle import (
    BaseThrottle,
    BurstThrottle,
    EndpointThrottle,
    GlobalThrottle,
    IPThrottle,
    ThrottleResult,
    UserTierThrottle,
    get_throttle,
    rate_limit,
)
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
from src.core.utils.crypto import FernetCipher
from src.core.utils.db import (
    dispose_all_engines,
    get_app_engine,
    get_async_engine,
    get_sessionmaker,
)
from src.core.utils.function_logger import log_function
from src.core.utils.http_client import AsyncAPIClient, AuthType
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
from src.core.utils.ssrf import assert_public_url

__all__ = [
    # Audit log
    "ApiLog",
    "ApiLogRepository",
    "RequestDirection",
    "close_repository",
    "get_repository",
    "init_repository",
    "log_inbound_request",
    "log_outbound_request",
    "outbound_response_meta_ctx",
    # Base
    "BaseCustomError",
    "BaseModel",
    "BaseNamedModelService",
    "BaseRepository",
    "BaseSchema",
    "BaseService",
    "EncryptedString",
    "NamedBaseModel",
    # Context
    "clear_request_context",
    "get_request_id",
    "request_id_ctx",
    "set_request_context",
    # Exceptions
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
    # Lifecycle
    "HealthCheckResult",
    "cache_check",
    "create_health_router",
    "create_readiness_router",
    "db_check",
    "throttle_check",
    # Middleware
    "ExceptionLoggingMiddleware",
    "RateLimitHeadersMiddleware",
    "RequestIDMiddleware",
    "RequestLoggingMiddleware",
    "SelectiveCORSMiddleware",
    "install_core_middleware",
    # Resilience
    "circuit_breaker",
    "resilience_registry",
    "resilient",
    "retry_on_failure",
    "retry_with_exponential_backoff",
    "BaseCacheBackend",
    "CacheVersionError",
    "bump_dataset_cache_version",
    "generate_cache_key",
    "get_cache",
    "get_cached_result",
    "get_dataset_cache_version",
    "set_cached_result",
    "BaseCircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "get_circuit_breaker_registry",
    "BaseThrottle",
    "BurstThrottle",
    "EndpointThrottle",
    "GlobalThrottle",
    "IPThrottle",
    "ThrottleResult",
    "UserTierThrottle",
    "get_throttle",
    "rate_limit",
    # Responses
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
    "AsyncAPIClient",
    "AsyncS3Client",
    "AsyncSESClient",
    "AuthType",
    "EmailAttachment",
    "EmailMessage",
    "FernetCipher",
    "RequestContextFilter",
    "assert_public_url",
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
