"""Grant project Skill management to built-in project administrators.

Revision ID: 20260908_27
Revises: 20260908_26
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_27"
down_revision: str | None = "20260908_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("id"),
        sa.column("unit_id"),
        sa.column("code"),
        sa.column("built_in"),
        sa.column("scope_type"),
    )
    grants = sa.table(
        "role_permissions",
        sa.column("id"),
        sa.column("unit_id"),
        sa.column("role_id"),
        sa.column("permission_code"),
        sa.column("data_scope"),
    )
    connection = op.get_bind()
    matching_roles = connection.execute(
        sa.select(roles.c.id, roles.c.unit_id).where(
            roles.c.code == "project_admin",
            roles.c.built_in.is_(True),
            roles.c.scope_type == "project",
        )
    ).mappings()
    for role in matching_roles:
        exists = connection.scalar(
            sa.select(grants.c.id).where(
                grants.c.role_id == role["id"],
                grants.c.permission_code == "skill.manage",
                grants.c.data_scope == "project",
            )
        )
        if exists is None:
            connection.execute(
                grants.insert().values(
                    id=str(uuid4()),
                    unit_id=role["unit_id"],
                    role_id=role["id"],
                    permission_code="skill.manage",
                    data_scope="project",
                )
            )


def downgrade() -> None:
    # A later manual grant is indistinguishable from the migrated grant.
    pass
