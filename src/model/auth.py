"""Auth ORM — ``User``, ``Role``, ``Permission``, ``APIKey``.

Modelled on the Django boilerplate's accounts app but adapted to async
SQLAlchemy 2.0:

* ``User`` — email-unique, ``is_active`` from ``BaseModel``, plus a
  M2M to ``Role``.
* ``Role`` — named bundle of ``Permission`` rows; one ``is_superuser_role``
  flag is the bypass switch (matches Django's pattern).
* ``Permission`` — atomic ``(resource, action)`` pair; the unique
  constraint keeps the catalogue from drifting.
* ``APIKey`` — service-to-service credential bound to a user. ``secret``
  is encrypted at rest via :class:`EncryptedString` (Fernet). A partial
  unique index on ``prefix`` scoped to active, non-revoked rows matches
  the exact predicate the authenticator filters by, so Postgres serves
  the lookup from the index alone.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.base.fields import EncryptedString
from src.core.base.model import BaseModel

# ── Association tables (no behaviour → kept as Core Tables) ──────────

user_roles = Table(
    "user_roles",
    BaseModel.metadata,
    Column(
        "user_id",
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

role_permissions = Table(
    "role_permissions",
    BaseModel.metadata,
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        BigInteger,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(BaseModel):
    """An atomic ``(resource, action)`` permission row.

    The unique constraint guarantees one row per pair so role-permission
    grants do not silently shadow each other.
    """

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),
    )

    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    roles: Mapped[list["Role"]] = relationship(
        secondary=role_permissions, back_populates="permissions", lazy="selectin"
    )

    def __str__(self) -> str:
        return f"{self.resource}:{self.action}"


class Role(BaseModel):
    """Named bundle of permissions.

    Superuser roles bypass the registry check entirely (their holders
    pass every :class:`RequireResource` dependency). ``is_default``
    roles are auto-assigned to new users — wire this in the user
    creation service once your user-provisioning flow lands.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_superuser_role: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )
    users: Mapped[list["User"]] = relationship(
        secondary=user_roles, back_populates="roles", lazy="selectin"
    )


class User(BaseModel):
    """Authenticated principal — owns API keys and holds roles.

    Email is unique at the DB level so admin / shell / management
    command paths cannot smuggle in a duplicate behind an
    application-level check.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def full_name(self) -> str:
        """Human-readable name, falling back to email when names are unset."""
        parts = [self.first_name or "", self.last_name or ""]
        joined = " ".join(p for p in parts if p).strip()
        return joined or self.email

    @property
    def has_superuser_role(self) -> bool:
        """Whether the user holds at least one ``is_superuser_role`` role."""
        return any(getattr(r, "is_superuser_role", False) for r in self.roles)


class APIKey(BaseModel):
    """Service-to-service credential bound to a user.

    The raw key is only known at creation time (``create_with_secret``
    returns it once). The stored ``secret`` is encrypted at rest via
    :class:`EncryptedString`; the 8-char ``prefix`` is the lookup key
    and is indexed for the auth-hot-path predicate.

    Soft-revocation (``revoked_at`` non-null) is preferred over a hard
    delete so audit trails referencing the key still resolve.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        # Partial unique index that matches the authenticator predicate:
        #   prefix = :p AND is_active AND revoked_at IS NULL
        # Postgres satisfies the lookup from the index alone — no heap
        # fetch on the common case.
        Index(
            "ix_api_keys_active_prefix",
            "prefix",
            unique=True,
            postgresql_where=(
                "is_active AND revoked_at IS NULL"  # type: ignore[arg-type]
            ),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    secret: Mapped[str] = mapped_column(EncryptedString(length=64), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    user: Mapped[User] = relationship(back_populates="api_keys", lazy="selectin")

    @property
    def is_revoked(self) -> bool:
        """Whether the soft-revocation timestamp has been stamped."""
        return self.revoked_at is not None


__all__ = ["APIKey", "Permission", "Role", "User", "role_permissions", "user_roles"]
