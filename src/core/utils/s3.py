"""``AsyncS3Client`` — async wrapper over the sync boto3 S3 client.

Every method runs the boto3 call inside ``asyncio.to_thread`` so the
event loop is never blocked. Each operation is wrapped in
``@resilient("s3")`` so transient failures retry and a streak of
failures opens the circuit breaker.

Dormant: not currently imported by any request-path file. Uncovered
until a feature wires the S3 helpers; do not import from a request-path
file without adding a matching integration test. Tracked by
``tests/unit/scripts/test_no_dormant_imports.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, BinaryIO
from urllib.parse import urlparse

from src.core.exceptions.infrastructure import S3Error
from src.core.exceptions.validation import ValidationError
from resilience_kit.decorators import resilient
from src.core.runtime import get_settings
from src.core.utils.aws import get_aws_client
from src.core.utils.log_sanitization import safe_log_dict

logger = logging.getLogger(__name__)

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_MAX_S3_JSON_SIZE = 10 * 1024 * 1024  # 10 MB


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` into ``(bucket, key)``.

    Args:
        s3_uri: Full ``s3://`` URI. Must include both bucket and key.

    Returns:
        Tuple ``(bucket, key)`` with the leading slash stripped from key.

    Raises:
        ValidationError: When the scheme is not ``s3`` or either component
            is missing.
    """
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValidationError(f"Invalid S3 URI: {s3_uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not key:
        raise ValidationError(f"S3 URI missing object key: {s3_uri}")
    return bucket, key


def build_s3_uri(bucket: str, key: str) -> str:
    """Compose ``s3://bucket/key``. Pure helper.

    Args:
        bucket: Bucket name.
        key: Object key (leading slash will be stripped).

    Returns:
        Canonical URI string (scheme s3, host bucket, path key).

    Raises:
        ValidationError: When either argument is empty.
    """
    if not bucket or not key:
        raise ValidationError(
            f"Both bucket and key required (got bucket={bucket!r}, key={key!r})."
        )
    return f"s3://{bucket}/{key.lstrip('/')}"


def generate_object_key(prefix: str, filename: str, *, ext: str | None = None) -> str:
    """UUID-prefixed key with date partition (``prefix/YYYY/MM/<uuid>__name.ext``).

    Args:
        prefix: Logical folder prefix; defaults to ``"assets"`` if empty.
        filename: Original filename (basename extracted; non-safe chars
            collapsed to ``-``).
        ext: Optional extension to append when not already present.

    Returns:
        S3 key shaped for human readability + date-based partitioning.
    """
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip() or "file"
    cleaned = _FILENAME_SANITIZE_RE.sub("-", base).strip("-._") or "file"
    if ext and not cleaned.lower().endswith(f".{ext.lower().lstrip('.')}"):
        cleaned = f"{cleaned}.{ext.lstrip('.')}"
    now = datetime.now(timezone.utc)
    folder = prefix.strip("/") or "assets"
    return f"{folder}/{now:%Y/%m}/{uuid.uuid4()}__{cleaned}"


def _classify_boto_exc(
    operation: str, bucket: str, key: str, exc: Exception
) -> S3Error:
    """Wrap a raw boto exception into the project's typed :class:`S3Error`.

    The original exception is preserved as ``__cause__`` via the
    caller's ``raise ... from exc``; this helper only constructs the
    typed wrapper so call sites can ``raise _classify_boto_exc(...)``.

    Args:
        operation: Logical operation name (``"upload_json"``, …) stored
            on the resulting ``S3Error``.
        bucket: Bucket the operation targeted.
        key: Object key the operation targeted.
        exc: The raw boto exception (used only for context; not stored).

    Returns:
        A typed ``S3Error`` ready to be raised.
    """
    return S3Error(
        f"S3 {operation} failed for {bucket}/{key}",
        operation=operation,
        bucket=bucket,
        key=key,
    )


def _is_not_found(exc: Exception) -> bool:
    """Detect a missing-key error from a boto3 ``ClientError``.

    Used by ``delete_object`` (idempotent delete) and ``object_exists``
    (404 → ``False``) — both want to swallow "no such key" without
    masking other failures.

    Args:
        exc: A boto3 exception (typically ``ClientError``).

    Returns:
        ``True`` when the error code is one of ``404``, ``NoSuchKey``,
        ``NotFound``; ``False`` otherwise.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code", "")
    return code in {"404", "NoSuchKey", "NotFound"}


class AsyncS3Client:
    """Static async methods for every S3 operation we use."""

    # ── Presigned URLs ────────────────────────────────────────────────

    @staticmethod
    @resilient("s3")
    async def generate_presigned_download_url(
        s3_uri: str,
        *,
        expiry: int | None = None,
    ) -> str:
        """Return a short-lived presigned URL the caller can ``GET``.

        Used to hand object access to a browser / external system
        without leaking AWS credentials. Default TTL comes from
        ``settings.s3_presigned_url_expiration``.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the object to share.
            expiry: Override the default TTL in seconds.

        Returns:
            Presigned HTTPS URL.

        Raises:
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = parse_s3_uri(s3_uri)
        settings = get_settings()
        ttl = expiry or settings.s3_presigned_url_expiration
        client = get_aws_client("s3")
        try:
            return await asyncio.to_thread(
                client.generate_presigned_url,
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=ttl,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "S3 presigned_download failed",
                extra=safe_log_dict(bucket=bucket, key=key, error=str(exc)),
            )
            raise _classify_boto_exc(
                "generate_presigned_download_url", bucket, key, exc
            ) from exc

    @staticmethod
    @resilient("s3")
    async def generate_presigned_upload_url(
        key: str,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
        expiry: int | None = None,
    ) -> dict[str, Any]:
        """Return a presigned ``PUT`` URL plus the bucket / key it targets.

        Lets the caller upload directly to S3 without proxying bytes
        through this service. ``content_type`` is enforced at PUT time
        if supplied.

        Args:
            key: Destination object key.
            bucket: Optional override; defaults to ``s3_default_bucket``.
            content_type: When set, baked into the signed URL so the
                client must send the matching ``Content-Type`` header.
            expiry: Override the default TTL in seconds.

        Returns:
            Dict of ``{url, key, bucket, content_type}``.

        Raises:
            ValidationError: When neither ``bucket`` nor ``s3_default_bucket`` is set.
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        settings = get_settings()
        bucket = bucket or settings.s3_default_bucket
        if not bucket:
            raise ValidationError("No bucket provided and s3_default_bucket is unset.")
        ttl = expiry or settings.s3_presigned_url_expiration
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        client = get_aws_client("s3")
        try:
            url = await asyncio.to_thread(
                client.generate_presigned_url,
                "put_object",
                Params=params,
                ExpiresIn=ttl,
            )
            return {
                "url": url,
                "key": key,
                "bucket": bucket,
                "content_type": content_type,
            }
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc(
                "generate_presigned_upload_url", bucket, key, exc
            ) from exc

    # ── JSON helpers ──────────────────────────────────────────────────

    @staticmethod
    @resilient("s3")
    async def fetch_json(
        s3_uri: str,
        *,
        max_size: int = _DEFAULT_MAX_S3_JSON_SIZE,
    ) -> dict[str, Any]:
        """Fetch a JSON object from S3 with a hard size cap.

        Rejects oversized objects *before* streaming the body, so a
        runaway object cannot exhaust memory. Default cap is 10 MB.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the JSON blob.
            max_size: Maximum permitted ``ContentLength`` in bytes.

        Returns:
            Parsed JSON as a dict.

        Raises:
            S3Error: Object too large or returns invalid JSON.
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = parse_s3_uri(s3_uri)
        client = get_aws_client("s3")
        try:
            response = await asyncio.to_thread(
                client.get_object, Bucket=bucket, Key=key
            )
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc("fetch_json", bucket, key, exc) from exc

        content_length = response.get("ContentLength", 0)
        if content_length > max_size:
            raise S3Error(
                f"S3 object too large ({content_length} bytes, max {max_size})",
                operation="fetch_json",
                bucket=bucket,
                key=key,
            )
        body = await asyncio.to_thread(response["Body"].read)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise S3Error(
                f"S3 object is not valid JSON: {bucket}/{key}",
                operation="fetch_json",
                bucket=bucket,
                key=key,
            ) from exc

    @staticmethod
    @resilient("s3")
    async def upload_json(
        data: dict[str, Any],
        *,
        bucket: str | None = None,
        key: str,
    ) -> str:
        """Serialise ``data`` and ``put_object`` it as ``application/json``.

        Args:
            data: JSON-serialisable payload.
            bucket: Optional override; defaults to ``s3_default_bucket``.
            key: Destination object key.

        Returns:
            Canonical URI string of the uploaded object.

        Raises:
            ValidationError: When neither ``bucket`` nor ``s3_default_bucket`` is set.
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        settings = get_settings()
        bucket = bucket or settings.s3_default_bucket
        if not bucket:
            raise ValidationError("No bucket provided and s3_default_bucket is unset.")
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        client = get_aws_client("s3")
        try:
            await asyncio.to_thread(
                client.put_object,
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc("upload_json", bucket, key, exc) from exc
        return build_s3_uri(bucket, key)

    # ── Binary helpers ────────────────────────────────────────────────

    @staticmethod
    @resilient("s3")
    async def fetch_bytes(s3_uri: str, *, max_size: int) -> tuple[bytes, str]:
        """Fetch arbitrary object bytes with a caller-imposed size cap.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the object.
            max_size: Maximum permitted ``ContentLength`` in bytes.

        Returns:
            Tuple ``(body_bytes, content_type)`` where ``content_type``
            falls back to ``application/octet-stream`` if S3 omits it.

        Raises:
            S3Error: Object is larger than ``max_size``.
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = parse_s3_uri(s3_uri)
        client = get_aws_client("s3")
        try:
            response = await asyncio.to_thread(
                client.get_object, Bucket=bucket, Key=key
            )
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc("fetch_bytes", bucket, key, exc) from exc

        content_length = response.get("ContentLength", 0)
        if content_length > max_size:
            raise S3Error(
                f"S3 object too large ({content_length} bytes, max {max_size})",
                operation="fetch_bytes",
                bucket=bucket,
                key=key,
            )
        body = await asyncio.to_thread(response["Body"].read)
        content_type = response.get("ContentType", "application/octet-stream")
        return body, content_type

    @staticmethod
    @resilient("s3")
    async def upload_file(
        file_obj: BinaryIO,
        *,
        bucket: str | None = None,
        key: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Stream a binary file object to S3 via ``upload_fileobj``.

        Used for multipart-friendly uploads where the caller has a
        file-like stream (e.g. a FastAPI ``UploadFile``).

        Args:
            file_obj: Open binary file-like (``read`` returning bytes).
            bucket: Optional override; defaults to ``s3_default_bucket``.
            key: Destination object key.
            content_type: MIME type stored on the object.
            metadata: Optional user metadata; keys and values are
                stringified before sending.

        Returns:
            Canonical URI string of the uploaded object.

        Raises:
            ValidationError: When neither ``bucket`` nor ``s3_default_bucket`` is set.
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        settings = get_settings()
        bucket = bucket or settings.s3_default_bucket
        if not bucket:
            raise ValidationError("No bucket provided and s3_default_bucket is unset.")
        extra_args: dict[str, Any] = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = {str(k): str(v) for k, v in metadata.items()}
        client = get_aws_client("s3")
        try:
            await asyncio.to_thread(
                client.upload_fileobj, file_obj, bucket, key, ExtraArgs=extra_args
            )
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc("upload_file", bucket, key, exc) from exc
        return build_s3_uri(bucket, key)

    # ── Object lifecycle ──────────────────────────────────────────────

    @staticmethod
    @resilient("s3")
    async def delete_object(s3_uri: str) -> None:
        """Idempotent delete — swallows ``NotFound`` so callers can blind-delete.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the object to remove.

        Raises:
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = parse_s3_uri(s3_uri)
        client = get_aws_client("s3")
        try:
            await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            if _is_not_found(exc):
                logger.info("delete_object: already absent (%s)", s3_uri)
                return
            raise _classify_boto_exc("delete", bucket, key, exc) from exc

    @staticmethod
    @resilient("s3")
    async def head_object(s3_uri: str) -> dict[str, Any]:
        """Return ``{size, content_type, etag, last_modified}`` for an object.

        Uses ``HeadObject`` (no body) so it's cheap to call on hot
        paths that only need metadata.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the object.

        Returns:
            Dict of metadata. ``etag`` is stripped of surrounding quotes.

        Raises:
            _classify_boto_exc: An ``S3Error`` wrapping the boto failure
                (including 404 — see :meth:`object_exists` for the
                swallow-404 variant).
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = parse_s3_uri(s3_uri)
        client = get_aws_client("s3")
        try:
            response = await asyncio.to_thread(
                client.head_object, Bucket=bucket, Key=key
            )
        except (ClientError, BotoCoreError) as exc:
            raise _classify_boto_exc("head", bucket, key, exc) from exc
        return {
            "size": response.get("ContentLength"),
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag", "").strip('"'),
            "last_modified": response.get("LastModified"),
        }

    @staticmethod
    async def object_exists(s3_uri: str) -> bool:
        """Return whether the object exists; ``False`` on 404; raise on other errors.

        Args:
            s3_uri: Full ``s3://bucket/key`` of the candidate object.

        Returns:
            ``True`` if the object exists, ``False`` if it does not.

        Raises:
            S3Error: For any S3 failure other than ``NotFound``.
        """
        try:
            await AsyncS3Client.head_object(s3_uri)
            return True
        except S3Error as exc:
            cause = exc.__cause__
            if isinstance(cause, Exception) and _is_not_found(cause):
                return False
            raise
