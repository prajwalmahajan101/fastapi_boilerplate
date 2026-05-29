"""Core base settings — project-independent.

Provides ``CoreSettings`` with shared infrastructure knobs (database, AWS,
Redis, logging, resilience defaults, API audit log) plus an AWS Secrets
Manager source. Application code should subclass ``CoreSettings`` in
``src/common/settings.py`` and add domain-specific fields there.

Settings priority (highest → lowest):
    1. AWS Secrets Manager (when ``aws_secret_name`` is set)
    2. Environment variables
    3. ``.env`` file
    4. Field defaults
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import PydanticBaseSettingsSource

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment,misc]
    NoCredentialsError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_project_root = Path(__file__).resolve().parent.parent.parent
_env_path = _project_root / ".env"


def load_aws_secrets(secret_name: str | None, region: str | None) -> dict[str, Any]:
    """Pull a secret blob from AWS Secrets Manager and return it as a dict.

    Returns an empty dict if ``secret_name`` is unset, boto3 is missing,
    credentials are absent, or the secret cannot be fetched. Failure is
    logged but never raised: settings must still load when AWS is offline.

    Args:
        secret_name: The AWS Secrets Manager secret identifier.
        region: AWS region; falls back to ``us-east-1`` if unset.

    Returns:
        The decoded JSON secret as a dict, or an empty dict on any failure.
    """
    if not secret_name:
        return {}

    if not _BOTO3_AVAILABLE:
        logger.warning("boto3 not installed; skipping AWS Secrets Manager.")
        return {}

    region = region or "us-east-1"

    try:
        client = boto3.client("secretsmanager", region_name=region)  # type: ignore[union-attr]
        response = client.get_secret_value(SecretId=secret_name)
    except NoCredentialsError:
        logger.warning("AWS credentials not found; skipping Secrets Manager.")
        return {}
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")  # type: ignore[union-attr]
        logger.error("AWS secret %s error: %s", secret_name, error_code)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error loading AWS secret %s: %s", secret_name, exc)
        return {}

    secret_string = response.get("SecretString")
    if not secret_string:
        return {}

    try:
        return json.loads(secret_string)
    except json.JSONDecodeError as exc:
        logger.error("AWS secret %s is not valid JSON: %s", secret_name, exc)
        return {}


class AwsSecretsSettingsSource(PydanticBaseSettingsSource):
    """Pydantic settings source backed by a pre-fetched AWS secret dict."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        aws_secrets: dict[str, Any],
    ) -> None:
        """Store the settings class and the pre-fetched AWS secret dict.

        Args:
            settings_cls: The settings class being constructed.
            aws_secrets: The pre-fetched secret blob to read from.
        """
        super().__init__(settings_cls)
        self.aws_secrets = aws_secrets

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Look up ``field_name`` (case-insensitive) in the AWS secret dict.

        Args:
            field: Pydantic field metadata (unused).
            field_name: The settings field to resolve.

        Returns:
            A ``(value, key, is_complex)`` tuple matching Pydantic's protocol.
        """
        for key, value in self.aws_secrets.items():
            if key.lower() == field_name.lower():
                return value, field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Collect every model field present in the AWS secret dict.

        Returns:
            A mapping of field name to resolved value for fields present.
        """
        out: dict[str, Any] = {}
        for field_name in self.settings_cls.model_fields:
            value, key, _ = self.get_field_value(None, field_name)
            if value is not None:
                out[key] = value
        return out


class CoreSettings(BaseSettings):
    """Project-independent base settings.

    Subclass in ``src/common/settings.py`` (``class Settings(CoreSettings):``)
    to add application-specific fields. Both the application's database
    session and the api_log Postgres backend read ``db_dsn`` from this class
    and resolve a shared engine via ``core.utils.db.get_async_engine``.
    """

    model_config = SettingsConfigDict(
        env_file=str(_env_path) if _env_path.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Identity ───────────────────────────────────────────────────────
    app_environment: str = "dev"

    # ── AWS ────────────────────────────────────────────────────────────
    aws_region: str = "ap-south-1"
    aws_secret_name: str | None = None

    # ── S3 ─────────────────────────────────────────────────────────────
    s3_default_bucket: str | None = None
    s3_presigned_url_expiration: int = 3600

    # ── SES ────────────────────────────────────────────────────────────
    ses_default_sender: str | None = None
    ses_region: str | None = None

    # ── Database (SHARED — app sessions + api_log backend share one pool) ─
    db_dsn: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "app"
    db_driver: str = "postgresql+asyncpg"
    db_pool_size: int = 5
    db_pool_max_overflow: int = 10
    db_pool_pre_ping: bool = True
    db_connect_timeout: int = 5
    db_statement_timeout_ms: int = 30_000

    @model_validator(mode="after")
    def _assemble_db_dsn(self) -> CoreSettings:
        if not self.db_dsn:
            self.db_dsn = (
                f"{self.db_driver}://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        return self

    # ── Encryption ─────────────────────────────────────────────────────
    # ``field_encryption_key`` is a Fernet key consumed by
    # ``EncryptedString`` columns and ``core.utils.crypto.FernetCipher``.
    # ``secret_key`` is a general-purpose app secret (token signing, etc.).
    field_encryption_key: str | None = None
    secret_key: str | None = None

    # ── SSRF ───────────────────────────────────────────────────────────
    ssrf_block_private_ips: bool = True

    # ── Response security headers ──────────────────────────────────────
    # Toggle for SecurityHeadersMiddleware (HSTS, X-Content-Type-Options,
    # X-Frame-Options, Referrer-Policy, Permissions-Policy, CSP). Default
    # on; deployments fronted by an upstream proxy that already injects
    # equivalent headers can set this to False to avoid duplication.
    security_headers_enabled: bool = True

    # ── Request body size cap ──────────────────────────────────────────
    # ContentLengthLimitMiddleware rejects any inbound request with a
    # body larger than this many bytes (returning 413). 1 MiB is a
    # generous default for JSON APIs while still capping accidental or
    # malicious huge payloads; raise it for endpoints that accept uploads.
    max_request_body_bytes: int = 1_048_576

    # ── OpenAPI docs surface ───────────────────────────────────────────
    # ``/docs`` (Swagger UI), ``/redoc``, and ``/openapi.json`` are off
    # by default because this is an s2s API — browser-rendered docs leak
    # the admin endpoint surface + error-code taxonomy on every public
    # URL. Dev / staging deployments opt in via ``OPENAPI_DOCS_ENABLED=true``;
    # production stays off. The relaxed CSP for ``/docs`` lives in
    # ``SecurityHeadersMiddleware`` and is path-scoped, so it is inert
    # when these endpoints are disabled.
    openapi_docs_enabled: bool = False

    # ── Logging ────────────────────────────────────────────────────────
    log_json: bool = True
    log_level: str = "INFO"
    log_force_reset: bool = False
    log_file_disabled: bool = True
    log_file: str = "app.log"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5
    log_function_calls: bool = False
    log_sanitize_max_string: int = 200
    log_sanitize_max_dict_keys: int = 20
    log_sanitize_max_list_items: int = 10

    # ── Networking ─────────────────────────────────────────────────────
    trust_proxy_headers: bool = False

    # ── Redis (named aliases — default / cache / rate_limit) ───────────
    redis_urls: dict[str, str] = Field(
        default_factory=lambda: {"default": "redis://localhost:6379/0"}
    )

    # ── Resilience defaults (merged with per-service overrides) ────────
    resilience_defaults: dict[str, Any] = Field(
        default_factory=lambda: {
            "circuit_breaker": {
                "failure_threshold": 5,
                "success_threshold": 2,
                "recovery_timeout": 30.0,
            },
            "retry": {
                "max_attempts": 3,
                "base_delay": 1.0,
                "exponential_base": 2.0,
                "max_delay": 10.0,
            },
        }
    )
    circuit_breaker_redis_alias: str = "default"
    circuit_breaker_key_prefix: str = "cb"

    # ── Rate limit ─────────────────────────────────────────────────────
    rate_limit_headers_enabled: bool = True
    rate_limit_redis_alias: str = "default"

    # ── Background tasks (Celery) ──────────────────────────────────────
    # The worker binary runs as ``celery -A src.core.tasks:celery_app
    # worker -Q <task_queue_name>``. The producer side
    # (``src.core.tasks.enqueue``) reads ``task_redis_alias`` and
    # ``task_queue_name``. ``celery_result_backend`` defaults to the
    # same Redis URL as the broker; set explicitly to swap result
    # storage (Postgres / RPC / disable). ``task_max_tries`` becomes
    # the Celery ``task_default_max_retries``.
    task_redis_alias: str = "default"
    task_queue_name: str = "default"
    task_max_tries: int = 5
    celery_result_backend: str | None = None

    # ── Metrics ────────────────────────────────────────────────────────
    # ``MetricsMiddleware`` tees per-request duration into the
    # ``src.core.metrics`` shim. Off by default — flip on once a
    # metrics exporter (Prometheus / OTel) is wired up. The shim
    # itself is always importable; toggling this flag only controls
    # whether the per-request emission happens.
    metrics_middleware_enabled: bool = False

    # ── API audit log (Postgres backend uses the shared db_dsn above) ──
    api_log_backend: Literal["noop", "postgres"] = "postgres"
    api_log_capture_request_body: bool = True
    api_log_capture_response_body: bool = True
    api_log_max_body_size: int = 10_000
    api_log_ttl_days: int = 30
    api_log_sensitive_headers: list[str] = Field(
        default_factory=lambda: [
            "authorization",
            "x-api-key",
            "cookie",
            "set-cookie",
            "proxy-authorization",
        ]
    )
    #: Seconds the lifespan shutdown will wait for in-flight audit
    #: writes to drain before forcing the process to exit. Bounded so a
    #: degraded audit backend cannot hang shutdown indefinitely.
    api_log_drain_timeout_seconds: float = 30.0
    #: Max rows the postgres backend flushes in a single bulk INSERT.
    #: Higher = fewer transactions but larger statements. Tune against
    #: the audit row width (bodies + headers + extra) so a flush stays
    #: comfortably under the statement size cap.
    api_log_batch_size: int = 100
    #: Max seconds the postgres backend waits to fill a batch before
    #: flushing whatever is buffered. Caps how long an audit row sits
    #: in memory before it lands in Postgres on a slow producer.
    api_log_batch_max_interval_seconds: float = 1.0
    #: Soft cap on the in-memory queue the postgres backend buffers
    #: against. Overflow drops the *newest* row with a warning (same
    #: contract as ``FireAndForgetQueue``) so a degraded Postgres
    #: cannot leak unbounded memory in the producer process.
    api_log_batch_queue_size: int = 5000

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Inject AWS Secrets Manager as the highest-priority source.

        Args:
            settings_cls: The settings class being constructed.
            init_settings: Source for ``__init__``-supplied values.
            env_settings: Source for environment variables.
            dotenv_settings: Source for ``.env`` file values.
            file_secret_settings: Source for file-based secrets.

        Returns:
            The ordered tuple of sources Pydantic should consult.
        """
        bootstrap: dict[str, Any] = {}
        if dotenv_settings:
            bootstrap.update(dotenv_settings())
        if env_settings:
            bootstrap.update(env_settings())

        aws_secrets = load_aws_secrets(
            bootstrap.get("aws_secret_name"),
            bootstrap.get("aws_region"),
        )
        aws_source = AwsSecretsSettingsSource(settings_cls, aws_secrets)

        # Highest priority first (Pydantic v2 stops at the first source
        # that returns a value). Matches the priority listed in the
        # module docstring: AWS Secrets > env > .env > file secrets.
        return (
            init_settings,
            aws_source,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
