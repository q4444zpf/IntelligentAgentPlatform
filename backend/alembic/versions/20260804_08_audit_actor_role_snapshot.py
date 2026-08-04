"""stabilize audit actor role snapshots

Revision ID: 20260804_08
Revises: 20260804_07
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260804_08"
down_revision: str | None = "20260804_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED = (
    "'unknown','user','project_admin','unit_auditor',"
    "'project_admin,user','project_admin,unit_auditor','unit_auditor,user',"
    "'project_admin,unit_auditor,user'"
)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("actor_role", sa.String(40), nullable=True, server_default="unknown"),
    )
    op.execute(sa.text("UPDATE agent_runs SET actor_role = 'unknown' WHERE actor_role IS NULL"))
    op.alter_column("agent_runs", "actor_role", nullable=False, server_default=None)

    op.execute(sa.text(
        "UPDATE audit_events SET actor_role = 'project_admin' WHERE actor_role = 'admin'"
    ))
    op.execute(sa.text(
        f"UPDATE audit_events SET actor_role = 'unknown' "
        f"WHERE actor_role IS NULL OR actor_role NOT IN ({_ALLOWED})"
    ))
    op.alter_column(
        "audit_events", "actor_role", existing_type=sa.String(40), nullable=False,
    )
    op.create_check_constraint(
        "ck_audit_actor_role",
        "audit_events",
        f"actor_role IN ({_ALLOWED})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_actor_role", "audit_events", type_="check")
    op.alter_column(
        "audit_events", "actor_role", existing_type=sa.String(40), nullable=True,
    )
    op.drop_column("agent_runs", "actor_role")
