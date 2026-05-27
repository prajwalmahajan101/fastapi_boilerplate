"""SQLAlchemy Core ``api_logs`` table — usable by Alembic and the Postgres backend."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

#: Separate MetaData — the audit log table lives outside the application ORM
#: registry so its DDL can be managed independently if desired.
metadata = MetaData()

api_logs = Table(
    "api_logs",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("log_id", Text, unique=True, nullable=False),
    Column("direction", Text, nullable=False),
    Column("service_name", Text, nullable=False),
    Column("request_id", Text),
    Column("environment", Text),
    Column("method", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("query_params", JSONB),
    Column("request_headers", JSONB),
    Column("request_body", Text),
    Column("response_status_code", Integer),
    Column("response_headers", JSONB),
    Column("response_body", Text),
    Column("duration_ms", Float),
    Column("error_type", Text),
    Column("error_message", Text),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("ttl_expires_at", BigInteger),
    Column("extra", JSONB),
    Index("idx_api_logs_request_id", "request_id"),
    Index(
        "idx_api_logs_direction_ts", "direction", "timestamp", postgresql_using="btree"
    ),
    Index("idx_api_logs_ts", "timestamp", postgresql_using="btree"),
)
