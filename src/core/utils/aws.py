"""Thread-local boto3 client cache.

boto3 clients are synchronous; we wrap their calls in ``asyncio.to_thread``
at the call sites (``AsyncS3Client``, ``AsyncSESClient``). One client per
``(service, region)`` per thread keeps connection reuse working with the
thread pool that ``asyncio.to_thread`` runs against.
"""

from __future__ import annotations

import threading
from typing import Any

from src.core.runtime import get_settings

_thread_local = threading.local()


def get_aws_client(service_name: str, *, region: str | None = None) -> Any:
    """Return a thread-local boto3 client for ``service_name`` in ``region``.

    Region defaults to ``CoreSettings.aws_region``. Credentials come
    from the standard boto3 chain — env vars, instance profile,
    ``~/.aws/credentials``.

    Args:
        service_name: AWS service identifier (``"s3"``, ``"ses"``, …).
        region: AWS region; falls back to the configured default.

    Returns:
        A boto3 client cached per ``(thread, service, region)``.
    """
    import boto3

    resolved_region = region or get_settings().aws_region
    cache_key = f"_aws_{service_name}_{resolved_region}".replace("-", "_")

    client = getattr(_thread_local, cache_key, None)
    if client is None:
        client = boto3.client(service_name, region_name=resolved_region)
        setattr(_thread_local, cache_key, client)
    return client
