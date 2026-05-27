"""No-op backend — discards every log silently. The default."""

from __future__ import annotations

from src.core.api_log.models import ApiLog
from src.core.api_log.repository import ApiLogRepository


class NoopApiLogRepository(ApiLogRepository):
    """Default repository for environments that do not persist audit logs."""

    async def save(self, log: ApiLog) -> None:  # noqa: ARG002 — interface stub
        """Discard the log entry silently.

        Args:
            log: A populated ``ApiLog`` record (ignored).
        """

    async def startup(self) -> None:
        """No-op startup hook."""

    async def shutdown(self) -> None:
        """No-op shutdown hook."""
