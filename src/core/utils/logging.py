"""Structured logging setup and request-id filter."""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.core.context import get_request_id
from src.core.runtime import get_settings

_DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(funcName)s | %(message)s | %(request_id)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
_MANAGED_TAG = "_core_managed"


class GZipRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that gzips rotated files in-place."""

    def doRollover(self) -> None:  # noqa: N802 — stdlib name
        """Rotate the log file, then gzip every retained backup in place.

        Calls the parent rotation (which shifts ``.1 → .2``, etc.), then
        walks the surviving backups and gzips any still on disk as
        plaintext. The rotation count semantics are unchanged — disk
        footprint just gets smaller per generation.
        """
        super().doRollover()
        for i in range(self.backupCount, 0, -1):
            log_file = f"{self.baseFilename}.{i}"
            gz_file = f"{log_file}.gz"
            if os.path.exists(log_file) and not os.path.exists(gz_file):
                with open(log_file, "rb") as f_in, gzip.open(gz_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(log_file)


class RequestContextFilter(logging.Filter):
    """Inject ``record.request_id`` from the async-local ContextVar."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the current request id to ``record`` (always returns True).

        Args:
            record: The ``LogRecord`` being emitted.

        Returns:
            Always ``True`` — this filter only enriches, never drops.
        """
        record.request_id = get_request_id() or getattr(record, "request_id", None)
        return True


def _validate_log_level(level_str: str) -> int:
    """Convert a stdlib log-level name to its numeric value.

    Args:
        level_str: One of ``CRITICAL``/``ERROR``/``WARNING``/``INFO``/
            ``DEBUG``/``NOTSET`` (case-insensitive).

    Returns:
        Numeric level (``logging.INFO`` etc.).

    Raises:
        ValueError: Unrecognised level name.
    """
    valid = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
    upper = level_str.upper()
    if upper not in valid:
        raise ValueError(f"Invalid log level: {upper}. Must be one of {valid}")
    return getattr(logging, upper)


def setup_logging() -> None:
    """Configure the root logger from the bound ``CoreSettings``.

    Idempotent — re-invocations short-circuit when a core-managed handler
    is already attached, so importing the module multiple times does not
    duplicate handlers.

    Raises:
        RuntimeError: File logging is enabled but the rotating file
            handler cannot be opened (e.g. permission denied on
            ``logs/``).
    """
    settings = get_settings()
    numeric_level = _validate_log_level(settings.log_level)

    if settings.log_json:
        try:
            from pythonjsonlogger.json import JsonFormatter

            formatter: logging.Formatter = JsonFormatter(
                fmt=_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATE_FORMAT
            )
        except ImportError:
            formatter = logging.Formatter(
                fmt=_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATE_FORMAT
            )
    else:
        formatter = logging.Formatter(
            fmt=_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATE_FORMAT
        )

    formatter.converter = time.gmtime  # UTC timestamps

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    if any(getattr(h, _MANAGED_TAG, False) for h in root_logger.handlers):
        return

    if settings.log_force_reset and root_logger.hasHandlers():
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _MANAGED_TAG, True)
    root_logger.addHandler(console_handler)

    if not settings.log_file_disabled:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / settings.log_file
        try:
            file_handler = GZipRotatingFileHandler(
                filename=str(log_path),
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
            )
            file_handler.setFormatter(formatter)
            setattr(file_handler, _MANAGED_TAG, True)
            root_logger.addHandler(file_handler)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to configure file logging: {exc}") from exc

    request_id_filter = RequestContextFilter()
    for handler in root_logger.handlers:
        handler.addFilter(request_id_filter)

    for noisy in ("boto3", "botocore", "urllib3", "s3transfer", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a stdlib logger, optionally named (typically ``__name__``).

    Thin wrapper kept so call sites import a single helper rather
    than ``logging.getLogger`` directly — makes future swaps to
    ``structlog`` etc. a one-touch change.

    Args:
        name: Logger name, typically ``__name__`` from the caller.

    Returns:
        Standard library logger.
    """
    return logging.getLogger(name)


def is_function_logging_enabled() -> bool:
    """Report whether ``@log_function`` should emit entry/exit logs.

    Reads the flag through ``get_settings()`` so an early-startup call
    (before ``configure(settings)`` runs) sees the safe ``False``
    default instead of crashing — important because decorators
    resolve at import time.

    Returns:
        ``True`` when ``CoreSettings.log_function_calls`` is on; ``False``
        otherwise, including when settings access fails for any reason.
    """
    try:
        return get_settings().log_function_calls
    except Exception:
        return False
