"""initial baseline — items, api_logs

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-27

Creates the two tables the boilerplate ships with:

* ``items`` — the example ``NamedBaseModel`` resource (delete it once your
  own models land, and regenerate this baseline).
* ``api_logs`` — the fire-and-forget API audit log (managed on its own
  ``MetaData`` in ``src.core.api_log.table``).

This mirrors what ``alembic revision --autogenerate`` produces from the
current models, so a fresh autogenerate against an up-to-date database
yields an empty diff. It is also the same schema
``python -m src.management.init_db`` creates via ``metadata.create_all``;
if you bootstrapped that way, run ``alembic stamp 0001_initial`` instead of
``upgrade`` so Alembic records the baseline without re-issuing the DDL.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
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
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_items_code"),
    )
    op.create_index("ix_items_is_active", "items", ["is_active"])

    op.create_table(
        "api_logs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("log_id", sa.Text(), nullable=False, unique=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "query_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "request_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column(
            "response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_expires_at", sa.BigInteger(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("idx_api_logs_request_id", "api_logs", ["request_id"])
    op.create_index(
        "idx_api_logs_direction_ts",
        "api_logs",
        ["direction", "timestamp"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_api_logs_ts",
        "api_logs",
        ["timestamp"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("idx_api_logs_ts", table_name="api_logs")
    op.drop_index("idx_api_logs_direction_ts", table_name="api_logs")
    op.drop_index("idx_api_logs_request_id", table_name="api_logs")
    op.drop_table("api_logs")

    op.drop_index("ix_items_is_active", table_name="items")
    op.drop_table("items")
