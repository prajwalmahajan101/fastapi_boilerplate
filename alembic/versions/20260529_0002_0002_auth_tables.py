"""auth tables — users, roles, permissions, api_keys, association rows

Revision ID: 0002_auth_tables
Revises: 0001_initial
Create Date: 2026-05-29

Adds the four tables wired up in ``src.model.auth`` plus the two
association tables that join them.

* ``users`` — authenticated principals.
* ``roles`` — named permission bundles, with an ``is_superuser_role``
  bypass flag.
* ``permissions`` — ``(resource, action)`` atomic ACL rows.
* ``api_keys`` — encrypted-at-rest credentials, soft-revocable via
  ``revoked_at``. The partial unique index matches the authenticator
  predicate so Postgres serves the lookup from the index alone.
* ``user_roles`` / ``role_permissions`` — many-to-many joins.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_auth_tables"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUDIT_COLUMNS = (
    sa.Column(
        "id", sa.BigInteger(), autoincrement=True, primary_key=True, nullable=False
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    ),
    sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
    ),
    sa.Column(
        "notes",
        sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ),
)


def upgrade() -> None:
    op.create_table(
        "users",
        *_AUDIT_COLUMNS,
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("first_name", sa.String(length=150), nullable=True),
        sa.Column("last_name", sa.String(length=150), nullable=True),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column("last_login_ip", sa.String(length=45), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_is_active", "users", ["is_active"])

    op.create_table(
        "roles",
        *_AUDIT_COLUMNS,
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "is_superuser_role",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("ix_roles_is_active", "roles", ["is_active"])

    op.create_table(
        "permissions",
        *_AUDIT_COLUMNS,
        sa.Column("resource", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.UniqueConstraint(
            "resource", "action", name="uq_permissions_resource_action"
        ),
    )
    op.create_index("ix_permissions_is_active", "permissions", ["is_active"])

    op.create_table(
        "api_keys",
        *_AUDIT_COLUMNS,
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("secret", sa.String(length=256), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_api_keys_user_id"
        ),
    )
    op.create_index("ix_api_keys_is_active", "api_keys", ["is_active"])
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index("ix_api_keys_revoked_at", "api_keys", ["revoked_at"])
    # Partial unique index matching the auth-hot-path predicate.
    op.create_index(
        "ix_api_keys_active_prefix",
        "api_keys",
        ["prefix"],
        unique=True,
        postgresql_where=sa.text("is_active AND revoked_at IS NULL"),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
    )


def downgrade() -> None:
    op.drop_table("role_permissions")
    op.drop_table("user_roles")
    op.drop_index("ix_api_keys_active_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_revoked_at", table_name="api_keys")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_is_active", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_permissions_is_active", table_name="permissions")
    op.drop_table("permissions")
    op.drop_index("ix_roles_is_active", table_name="roles")
    op.drop_table("roles")
    op.drop_index("ix_users_is_active", table_name="users")
    op.drop_table("users")
