from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_admin_context

from .models import (
    Permission,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
)
from .schemas import (
    AdminPermission,
    AdminProject,
    AdminProjectMembership,
    AdminRole,
    AdminRoleSummary,
    AdminUnit,
    AdminUser,
)

router = APIRouter()


@router.get("/users", response_model=list[AdminUser])
def list_users(
    context: Annotated[RequestContext, Depends(require_admin_context)],
    session: Session = Depends(get_session),
) -> list[AdminUser]:
    rows = session.execute(
        select(User, UnitMembership.status)
        .join(UnitMembership, UnitMembership.user_id == User.id)
        .where(UnitMembership.unit_id == context.unit_id)
        .order_by(User.display_name, User.id)
    ).all()
    user_ids = [user.id for user, _ in rows]
    projects = session.execute(
        select(ProjectMembership, Project)
        .join(Project, (Project.id == ProjectMembership.project_id) & (Project.unit_id == ProjectMembership.unit_id))
        .where(ProjectMembership.unit_id == context.unit_id, ProjectMembership.user_id.in_(user_ids))
        .order_by(Project.name, Project.id)
    ).all() if user_ids else []
    project_memberships: dict[str, list[AdminProjectMembership]] = {}
    for membership, project in projects:
        project_memberships.setdefault(membership.user_id, []).append(
            AdminProjectMembership(
                project_id=project.id, project_code=project.code,
                project_name=project.name, status=membership.status,
            )
        )
    unit_role_rows = session.execute(
        select(UnitMembershipRole.user_id, Role)
        .join(Role, Role.id == UnitMembershipRole.role_id)
        .where(UnitMembershipRole.unit_id == context.unit_id, UnitMembershipRole.user_id.in_(user_ids))
    ).all() if user_ids else []
    project_role_rows = session.execute(
        select(ProjectMembershipRole.user_id, Role, ProjectMembershipRole.project_id)
        .join(Role, Role.id == ProjectMembershipRole.role_id)
        .where(ProjectMembershipRole.unit_id == context.unit_id, ProjectMembershipRole.user_id.in_(user_ids))
    ).all() if user_ids else []
    role_summaries: dict[str, list[AdminRoleSummary]] = {}
    for user_id, role in unit_role_rows:
        role_summaries.setdefault(user_id, []).append(
            AdminRoleSummary(role_id=role.id, code=role.code, name=role.name, scope_type=role.scope_type)
        )
    for user_id, role, project_id in project_role_rows:
        role_summaries.setdefault(user_id, []).append(
            AdminRoleSummary(role_id=role.id, code=role.code, name=role.name, scope_type=role.scope_type, project_id=project_id)
        )
    return [
        AdminUser.from_row(user, membership_status, project_memberships.get(user.id, ()), role_summaries.get(user.id, ()))
        for user, membership_status in rows
    ]


@router.get("/units", response_model=list[AdminUnit])
def list_units(
    context: Annotated[RequestContext, Depends(require_admin_context)],
    session: Session = Depends(get_session),
) -> list[AdminUnit]:
    unit = session.scalar(select(Unit).where(Unit.id == context.unit_id))
    return [] if unit is None else [AdminUnit.model_validate(unit, from_attributes=True)]


@router.get("/projects", response_model=list[AdminProject])
def list_projects(
    context: Annotated[RequestContext, Depends(require_admin_context)],
    session: Session = Depends(get_session),
) -> list[AdminProject]:
    rows = session.scalars(
        select(Project).where(Project.unit_id == context.unit_id).order_by(Project.name, Project.id)
    ).all()
    return [AdminProject.model_validate(project, from_attributes=True) for project in rows]


@router.get("/roles", response_model=list[AdminRole])
def list_roles(
    context: Annotated[RequestContext, Depends(require_admin_context)],
    session: Session = Depends(get_session),
) -> list[AdminRole]:
    rows = session.scalars(
        select(Role)
        .where((Role.unit_id == context.unit_id) | (Role.scope_type == "platform"))
        .order_by(Role.name, Role.id)
    ).all()
    return [AdminRole.model_validate(role, from_attributes=True) for role in rows]


@router.get("/permissions", response_model=list[AdminPermission])
def list_permissions(
    context: Annotated[RequestContext, Depends(require_admin_context)],
    session: Session = Depends(get_session),
) -> list[AdminPermission]:
    rows = session.scalars(select(Permission).order_by(Permission.resource, Permission.action, Permission.code)).all()
    return [AdminPermission.model_validate(permission, from_attributes=True) for permission in rows]
