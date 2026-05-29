"""Infrastructure exceptions — external systems, circuit breakers, encryption."""

from __future__ import annotations

from typing import Any

from src.core.base.exception import BaseCustomError


class InfrastructureError(BaseCustomError):
    """Base for failures in non-domain subsystems (caches, queues, encryption, …)."""

    default_message = "An internal system error occurred."
    error_code = "INFRASTRUCTURE_ERROR"
    status_code = 500


class ServiceUnavailableError(InfrastructureError):
    """A circuit breaker is OPEN; the call was short-circuited.

    Carries ``service_name`` so dashboards and clients can identify which
    dependency is being protected.
    """

    default_message = "Service is currently unavailable."
    error_code = "SERVICE_UNAVAILABLE"
    status_code = 503

    def __init__(self, service_name: str, message: str | None = None) -> None:
        """Capture the name of the dependency whose breaker is OPEN.

        Args:
            service_name: Identifier of the protected dependency.
            message: Optional override message.
        """
        self.service_name = service_name
        super().__init__(
            message
            or f"Service '{service_name}' is currently unavailable (circuit breaker open)."
        )

    def get_details(self) -> dict[str, Any]:
        """Return the ``service_name`` in the error details payload.

        Returns:
            ``{"service_name": <name>}``.
        """
        return {"service_name": self.service_name}


class ExternalServiceError(InfrastructureError):
    """Catch-all for outbound call failures not covered by a more specific subclass."""

    default_message = "An external service error occurred."
    error_code = "EXTERNAL_SERVICE_ERROR"
    status_code = 502


class TransientError(ExternalServiceError):
    """Temporary external failure expected to resolve on retry.

    The retry decorator inspects this class to decide whether to attempt
    again — promote a failure to ``TransientError`` only when the same
    request issued again has a reasonable chance of succeeding.
    """

    default_message = "A temporary failure occurred. Please retry."
    error_code = "TRANSIENT_ERROR"
    status_code = 502


class ExternalTimeoutError(ExternalServiceError):
    """Outbound call did not complete within the configured timeout."""

    default_message = "External service call timed out."
    error_code = "EXTERNAL_TIMEOUT"
    status_code = 502


class S3Error(ExternalServiceError):
    """An S3 operation failed (upload, download, head, presign, delete)."""

    default_message = "An S3 operation failed."
    error_code = "S3_ERROR"
    status_code = 502

    def __init__(
        self,
        message: str | None = None,
        *,
        operation: str | None = None,
        bucket: str | None = None,
        key: str | None = None,
    ) -> None:
        """Capture which S3 operation failed and on what bucket/key.

        Args:
            message: Optional override message.
            operation: S3 API name (``get_object``, ``put_object``, etc.).
            bucket: Target bucket name, if any.
            key: Target object key, if any.
        """
        self.operation = operation
        self.bucket = bucket
        self.key = key
        super().__init__(message or self.default_message)

    def get_details(self) -> dict[str, Any]:
        """Return operation/bucket/key for the envelope details.

        Returns:
            Dict with ``operation``, ``bucket``, and ``key``.
        """
        return {"operation": self.operation, "bucket": self.bucket, "key": self.key}


class SESError(ExternalServiceError):
    """A non-retryable SES email failure (invalid sender, sandbox limit, …)."""

    default_message = "An SES email operation failed."
    error_code = "SES_ERROR"
    status_code = 502

    def __init__(
        self,
        message: str | None = None,
        *,
        operation: str | None = None,
    ) -> None:
        """Capture which SES operation failed.

        Args:
            message: Optional override message.
            operation: SES API name (``send_email``, ``send_raw_email``, …).
        """
        self.operation = operation
        super().__init__(message or self.default_message)

    def get_details(self) -> dict[str, Any]:
        """Return the SES operation in the envelope details.

        Returns:
            ``{"operation": <name>}``.
        """
        return {"operation": self.operation}


class UpstreamPushError(ExternalServiceError):
    """Pushing data to an upstream API failed (non-2xx status or transport error)."""

    default_message = "Failed to push data to an upstream service."
    error_code = "UPSTREAM_PUSH_ERROR"
    status_code = 502


class DecryptionError(InfrastructureError):
    """An ``EncryptedString`` column could not be decrypted (key rotation / corruption)."""

    default_message = "Failed to decrypt field value."
    error_code = "DECRYPTION_ERROR"
    status_code = 500


class OutboundURLNotAllowedError(InfrastructureError):
    """An outbound HTTP call targeted a host not in ``outbound_url_allowlist``.

    Defence-in-depth alongside the SSRF guard — the SSRF check blocks
    private addresses, this exception blocks legitimate public hosts
    the service was never supposed to talk to.
    """

    default_message = "Outbound URL is not in the allow-list."
    error_code = "OUTBOUND_URL_NOT_ALLOWED"
    status_code = 502
