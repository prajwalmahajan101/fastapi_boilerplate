"""Field-level Fernet encryption (AES-128-CBC + HMAC).

Single source of truth for ``EncryptedString`` (SQLAlchemy ``TypeDecorator``)
and any code that needs round-trip plaintext ↔ ciphertext.

Key derivation:
    * ``CoreSettings.field_encryption_key`` (required in prod) is hashed with
      SHA-256 and the digest is used as the Fernet key. A dedicated key
      means routine rotation of any other secret cannot accidentally
      corrupt encrypted columns.
    * In non-prod, falls back to ``CoreSettings.secret_key`` with a warning
      so local development without a key still boots.
    * In prod, the absence of ``field_encryption_key`` is a configuration
      error — refuse to encrypt rather than silently produce data you
      cannot read after the next key rotation.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import logging

from src.core.exceptions.infrastructure import DecryptionError, InfrastructureError
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)


class FernetUnavailableError(InfrastructureError):
    """``cryptography`` is not installed (Fernet cannot be loaded)."""

    default_message = "Fernet encryption library is not available."
    error_code = "FERNET_UNAVAILABLE"


class EncryptionConfigError(InfrastructureError):
    """``field_encryption_key`` missing in a production-like environment."""

    default_message = "field_encryption_key must be set in non-dev environments."
    error_code = "ENCRYPTION_CONFIG_ERROR"


@functools.lru_cache(maxsize=1)
def _fernet():
    """Return the process-wide ``Fernet`` instance, building it on first call.

    Cached so every encrypt/decrypt round trip uses the same key without
    re-deriving the SHA-256 digest. In production-like environments the
    derivation requires ``field_encryption_key``; non-prod falls back to
    ``secret_key`` with a warning so local dev still boots.

    Returns:
        Configured ``Fernet`` instance ready for ``encrypt`` / ``decrypt``.

    Raises:
        FernetUnavailableError: ``cryptography`` is not installed.
        EncryptionConfigError: ``field_encryption_key`` is unset in a
            non-dev environment, or neither key is set anywhere.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise FernetUnavailableError() from exc

    settings = get_settings()
    key_source = settings.field_encryption_key
    if not key_source:
        if settings.app_environment.lower() not in {
            "dev",
            "development",
            "test",
            "local",
        }:
            raise EncryptionConfigError(
                "field_encryption_key must be set in non-dev environments. "
                "Silent fallback to secret_key is disabled to prevent data "
                "corruption on key rotation."
            )
        logger.warning(
            "field_encryption_key not set; falling back to secret_key (non-prod only)."
        )
        if not settings.secret_key:
            raise EncryptionConfigError(
                "Neither field_encryption_key nor secret_key is set."
            )
        key_source = settings.secret_key
    digest = hashlib.sha256(key_source.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class FernetCipher:
    """Encrypt/decrypt strings with the application's Fernet key."""

    @staticmethod
    def encrypt(plaintext: str) -> str:
        """Encrypt ``plaintext`` with the configured Fernet key.

        Empty strings (``""``) pass through unchanged so they survive
        an ``EncryptedString`` round-trip without forcing a sentinel
        value in the database.

        Args:
            plaintext: UTF-8 source text to encrypt.

        Returns:
            Fernet ciphertext (URL-safe base64), or the original empty
            string when input was empty.
        """
        if not plaintext:
            return plaintext
        return _fernet().encrypt(plaintext.encode()).decode()

    @staticmethod
    def decrypt(ciphertext: str) -> str:
        """Decrypt ``ciphertext`` with the configured Fernet key.

        Empty strings pass through unchanged, mirroring :meth:`encrypt`.
        ``InvalidToken`` is converted into a domain-specific
        :class:`DecryptionError` so callers can map it onto a 500
        response without exposing crypto internals.

        Args:
            ciphertext: Fernet token (URL-safe base64).

        Returns:
            Decrypted plaintext, or the original empty string.

        Raises:
            FernetUnavailableError: ``cryptography`` is not installed.
            DecryptionError: The token is invalid (key rotation or
                corruption).
        """
        if not ciphertext:
            return ciphertext
        try:
            from cryptography.fernet import InvalidToken
        except ImportError as exc:
            raise FernetUnavailableError() from exc

        try:
            return _fernet().decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            logger.error(
                "EncryptedString decryption failed — possible key rotation or data corruption."
            )
            raise DecryptionError(
                "Failed to decrypt field value. Check field_encryption_key configuration."
            ) from exc
