"""expand MCP client management module

Revision ID: 20260810_14
Revises: 20260809_13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_14"
down_revision: str | None = "20260809_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mcp_clients", sa.Column("client_id", sa.String(128), nullable=True))
    op.add_column("mcp_clients", sa.Column("unit_id", sa.String(128), nullable=True))
    op.add_column("mcp_clients", sa.Column("credential_id", sa.String(128), nullable=True))
    op.add_column("mcp_clients", sa.Column("status", sa.String(32), nullable=False, server_default="active"))
    op.add_column("mcp_clients", sa.Column("health_status", sa.String(32), nullable=False, server_default="not_checked"))
    op.add_column("mcp_clients", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mcp_clients", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mcp_clients", sa.Column("last_latency_ms", sa.Integer(), nullable=True))
    op.add_column("mcp_clients", sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("mcp_clients", sa.Column("last_error_code", sa.String(64), nullable=True))
    op.add_column("mcp_clients", sa.Column("last_error_message", sa.Text(), nullable=True))
    op.add_column("mcp_clients", sa.Column("health_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("update mcp_clients set client_id = client_key where client_id is null"))
    op.create_unique_constraint("uq_mcp_clients_client_id", "mcp_clients", ["client_id"])
    op.create_index("ix_mcp_clients_unit_status", "mcp_clients", ["unit_id", "status"])

    op.create_table(
        "mcp_project_grants",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("unit_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("client_id", "project_id", name="uq_mcp_project_grant"),
    )
    op.create_index("ix_mcp_project_grants_unit_project", "mcp_project_grants", ["unit_id", "project_id"])

    op.create_table(
        "mcp_tools",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("schema_hash", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="unpublished"),
        sa.Column("source_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("client_id", "name", name="uq_mcp_tool_name"),
    )
    op.create_index("ix_mcp_tools_client_available", "mcp_tools", ["client_id", "source_available"])

    op.create_table(
        "mcp_health_checks",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mcp_health_checks_client_checked", "mcp_health_checks", ["client_id", "checked_at"])

    op.create_table(
        "mcp_operations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="queued"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_operations_client_created", "mcp_operations", ["client_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_operations_client_created", table_name="mcp_operations")
    op.drop_table("mcp_operations")
    op.drop_index("ix_mcp_health_checks_client_checked", table_name="mcp_health_checks")
    op.drop_table("mcp_health_checks")
    op.drop_index("ix_mcp_tools_client_available", table_name="mcp_tools")
    op.drop_table("mcp_tools")
    op.drop_index("ix_mcp_project_grants_unit_project", table_name="mcp_project_grants")
    op.drop_table("mcp_project_grants")
    op.drop_index("ix_mcp_clients_unit_status", table_name="mcp_clients")
    op.drop_constraint("uq_mcp_clients_client_id", "mcp_clients", type_="unique")
    for column in ("health_lease_until", "last_error_message", "last_error_code", "failure_count", "last_latency_ms", "last_success_at", "last_checked_at", "health_status", "status", "credential_id", "unit_id", "client_id"):
        op.drop_column("mcp_clients", column)
