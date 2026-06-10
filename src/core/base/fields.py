"""``EncryptedString`` — SQLAlchemy column type that encrypts at rest.

Plaintext goes in, ciphertext lands in the database, plaintext comes back
out. Empty strings and ``None`` are stored as-is (not encrypted). On
decryption failure raises ``DecryptionError`` so a corrupted or
key-rotated value surfaces loudly instead of returning garbage.

Storage width:
    Fernet ciphertext is ~50% longer than plaintext plus base64 padding,
    so ``EncryptedString(length=L)`` allocates roughly ``L * 2 + 128`` for
    the underlying ``String`` column. Override by passing an explicit
    ``length`` if you need precise control.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from resilience_kit import FernetCipher


class EncryptedString(TypeDecorator):
    """SQLAlchemy ``String`` that transparently encrypts via Fernet."""

    impl = String
    cache_ok = True

    def __init__(self, length: int = 255, **kwargs: Any) -> None:
        """Allocate column width to fit Fernet-encrypted output for plaintext.

        Args:
            length: Max plaintext length; physical column is wider.
            **kwargs: Forwarded to the underlying ``String`` column.
        """
        self._plain_length = length
        super().__init__(length=length * 2 + 128, **kwargs)

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt the value on its way into the database.

        Args:
            value: The plaintext value (or ``None``/empty string).
            dialect: The SQLAlchemy dialect (unused).

        Returns:
            Ciphertext, or the original value if ``None`` or empty.
        """
        if value is None or value == "":
            return value
        return FernetCipher.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt the value on its way out of the database.

        Args:
            value: The ciphertext value (or ``None``/empty string).
            dialect: The SQLAlchemy dialect (unused).

        Returns:
            Plaintext, or the original value if ``None`` or empty.
        """
        if value is None or value == "":
            return value
        return FernetCipher.decrypt(value)
