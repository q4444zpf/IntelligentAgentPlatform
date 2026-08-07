from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_admin_context

from .models import (
    ExternalIdentity, Permission,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
    new_id,
)
from .schemas import (
    AdminPermission,
    AdminProject,
    AdminProjectMembership,
    AdminRole,
    AdminRoleSummary,
    AdminUnit,
    AdminUser,
    AssignIdentityRoleRequest, BindExternalIdentityRequest, CreateIdentityUserRequest,
    IdentityStatusRequest, UpdateIdentityUserRequest,
    CreateProjectRequest, UpdateProjectRequest, ProjectStatusRequest,
    CreateRoleRequest, RoleStatusRequest, GrantPermissionRequest,
)

router = APIRouter()


def identity_admin_context(
    admin_user_header: str | None = Header(default=None, alias="X-User-ID"),
    admin_project_header: str | None = Header(default=None, alias="X-Project-ID"),
    admin_unit_header: str | None = Header(default=None, alias="X-Unit-ID"),
    admin_role_header: str | None = Header(default=None, alias="X-User-Role"),
    admin_roles_header: str | None = Header(default=None, alias="X-User-Roles"),
) -> RequestContext:
    from fastapi import HTTPException
    if not admin_user_header or not admin_project_header or not admin_unit_header:
        raise HTTPException(status_code=401, detail="Unit, user, and project headers are required")
    roles = {value.strip() for value in (admin_roles_header or "").split(",") if value.strip()}
    if not roles and admin_role_header:
        roles = {"project_admin" if admin_role_header == "admin" else admin_role_header}
    if not roles:
        roles = {"user"}
    if not roles & {"unit_admin", "project_admin"}:
        raise HTTPException(status_code=403, detail="Administrator permission is required")
    return RequestContext(user_id=admin_user_header, project_id=admin_project_header, unit_id=admin_unit_header, roles=frozenset(roles))


def _unit_member(session: Session, user_id: str, unit_id: str) -> UnitMembership | None:
    return session.scalar(select(UnitMembership).where(UnitMembership.user_id == user_id, UnitMembership.unit_id == unit_id))


def _ensure_project(session: Session, project_id: str | None, unit_id: str) -> Project | None:
    if project_id is None:
        return None
    project = session.scalar(select(Project).where(Project.id == project_id, Project.unit_id == unit_id))
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在或不属于当前单位")
    return project


def _bump(user: User) -> None:
    user.authorization_version += 1


@router.get("/users", response_model=list[AdminUser])
def list_users(
    context: RequestContext = Depends(identity_admin_context),
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


@router.post("/users", response_model=AdminUser, status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateIdentityUserRequest,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> AdminUser:
    project = _ensure_project(session, body.project_id, context.unit_id)
    user = User(id=new_id(), display_name=body.display_name, email=body.email, status="active", authorization_version=1)
    session.add(user)
    session.flush()
    session.add(UnitMembership(id=new_id(), user_id=user.id, unit_id=context.unit_id, status="active"))
    if project is not None:
        session.add(ProjectMembership(id=new_id(), user_id=user.id, unit_id=context.unit_id, project_id=project.id, status="active"))
    session.commit()
    return AdminUser.from_row(user, "active")


@router.patch("/users/{user_id}", response_model=AdminUser)
def update_user(
    body: UpdateIdentityUserRequest, user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> AdminUser:
    membership = _unit_member(session, user_id, context.unit_id)
    user = session.scalar(select(User).where(User.id == user_id))
    if membership is None or user is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    user.display_name, user.email = body.display_name, body.email
    _bump(user)
    session.commit()
    return AdminUser.from_row(user, membership.status)


@router.post("/users/{user_id}/status", response_model=AdminUser)
def set_user_status(
    body: IdentityStatusRequest, user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> AdminUser:
    membership = _unit_member(session, user_id, context.unit_id)
    user = session.scalar(select(User).where(User.id == user_id))
    if membership is None or user is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    if body.status == "inactive":
        admin_count = session.scalar(select(User.id).join(UnitMembership, UnitMembership.user_id == User.id).join(UnitMembershipRole, UnitMembershipRole.user_id == User.id).join(Role, Role.id == UnitMembershipRole.role_id).where(UnitMembership.unit_id == context.unit_id, UnitMembership.status == "active", User.status == "active", Role.code.in_(["unit_admin", "project_admin"])).limit(2))
        admins = session.execute(select(User.id).join(UnitMembership, UnitMembership.user_id == User.id).join(UnitMembershipRole, UnitMembershipRole.user_id == User.id).join(Role, Role.id == UnitMembershipRole.role_id).where(UnitMembership.unit_id == context.unit_id, UnitMembership.status == "active", User.status == "active", Role.code.in_(["unit_admin", "project_admin"]))).all()
        if user.status == "active" and len({row[0] for row in admins}) <= 1 and user.id in {row[0] for row in admins}:
            raise HTTPException(status_code=409, detail="不能停用单位最后一名管理员")
    user.status = body.status
    membership.status = body.status
    _bump(user)
    session.commit()
    return AdminUser.from_row(user, membership.status)


@router.post("/users/{user_id}/external-identities", status_code=status.HTTP_201_CREATED)
def bind_external_identity(
    body: BindExternalIdentityRequest, user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    user = session.scalar(select(User).join(UnitMembership, UnitMembership.user_id == User.id).where(User.id == user_id, UnitMembership.unit_id == context.unit_id))
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    exists = session.scalar(select(ExternalIdentity).where(ExternalIdentity.issuer == body.issuer, ExternalIdentity.subject == body.subject))
    if exists is not None:
        raise HTTPException(status_code=409, detail="外部身份已绑定")
    session.add(ExternalIdentity(id=new_id(), user_id=user.id, issuer=body.issuer, subject=body.subject, claims={}, last_login_at=datetime.now(timezone.utc)))
    _bump(user)
    session.commit()
    return {"user_id": user.id, "issuer": body.issuer, "subject": body.subject}


@router.post("/users/{user_id}/roles", status_code=status.HTTP_201_CREATED)
def assign_role(
    body: AssignIdentityRoleRequest, user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str | None]:
    membership = _unit_member(session, user_id, context.unit_id)
    role = session.scalar(select(Role).where(Role.id == body.role_id, (Role.unit_id == context.unit_id) | (Role.scope_type == "platform")))
    project = _ensure_project(session, body.project_id, context.unit_id)
    if membership is None or role is None:
        raise HTTPException(status_code=404, detail="用户或角色不存在或无权访问")
    if project is None:
        if role.scope_type != "unit":
            raise HTTPException(status_code=422, detail="该角色必须绑定项目")
        exists = session.scalar(select(UnitMembershipRole).where(UnitMembershipRole.user_id == user_id, UnitMembershipRole.unit_id == context.unit_id, UnitMembershipRole.role_id == role.id))
        if exists is None:
            session.add(UnitMembershipRole(id=new_id(), user_id=user_id, unit_id=context.unit_id, role_id=role.id, scope_type="unit"))
    else:
        if role.scope_type != "project":
            raise HTTPException(status_code=422, detail="单位角色不能绑定单个项目")
        pm = session.scalar(select(ProjectMembership).where(ProjectMembership.user_id == user_id, ProjectMembership.unit_id == context.unit_id, ProjectMembership.project_id == project.id))
        if pm is None:
            pm = ProjectMembership(id=new_id(), user_id=user_id, unit_id=context.unit_id, project_id=project.id, status="active")
            session.add(pm); session.flush()
        exists = session.scalar(select(ProjectMembershipRole).where(ProjectMembershipRole.user_id == user_id, ProjectMembershipRole.unit_id == context.unit_id, ProjectMembershipRole.project_id == project.id, ProjectMembershipRole.role_id == role.id))
        if exists is None:
            session.add(ProjectMembershipRole(id=new_id(), user_id=user_id, unit_id=context.unit_id, project_id=project.id, role_id=role.id, scope_type="project"))
    _bump(session.scalar(select(User).where(User.id == user_id)))
    session.commit()
    return {"user_id": user_id, "role_id": role.id, "project_id": project.id if project else None}
@router.get("/units", response_model=list[AdminUnit])
def list_units(
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminUnit]:
    unit = session.scalar(select(Unit).where(Unit.id == context.unit_id))
    return [] if unit is None else [AdminUnit.model_validate(unit, from_attributes=True)]


@router.get("/projects", response_model=list[AdminProject])
def list_projects(
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminProject]:
    rows = session.scalars(
        select(Project).where(Project.unit_id == context.unit_id).order_by(Project.name, Project.id)
    ).all()
    return [AdminProject.model_validate(project, from_attributes=True) for project in rows]


@router.post("/projects", response_model=AdminProject, status_code=status.HTTP_201_CREATED)
def create_project(body: CreateProjectRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> AdminProject:
    if session.scalar(select(Project).where(Project.unit_id == context.unit_id, Project.code == body.code)) is not None:
        raise HTTPException(status_code=409, detail="项目编码已存在")
    project = Project(id=new_id(), unit_id=context.unit_id, code=body.code, name=body.name, status="active")
    session.add(project); session.commit()
    return AdminProject.model_validate(project, from_attributes=True)


@router.patch("/projects/{project_id}", response_model=AdminProject)
def update_project(project_id: str, body: UpdateProjectRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> AdminProject:
    project = _ensure_project(session, project_id, context.unit_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在或不属于当前单位")
    project.name = body.name; session.commit()
    return AdminProject.model_validate(project, from_attributes=True)


@router.post("/projects/{project_id}/status", response_model=AdminProject)
def set_project_status(project_id: str, body: ProjectStatusRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> AdminProject:
    project = _ensure_project(session, project_id, context.unit_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在或不属于当前单位")
    project.status = body.status; session.commit()
    return AdminProject.model_validate(project, from_attributes=True)


@router.get("/roles", response_model=list[AdminRole])
def list_roles(
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminRole]:
    rows = session.scalars(
        select(Role)
        .where((Role.unit_id == context.unit_id) | (Role.scope_type == "platform"))
        .order_by(Role.name, Role.id)
    ).all()
    return [AdminRole.model_validate(role, from_attributes=True) for role in rows]


@router.post("/roles", response_model=AdminRole, status_code=status.HTTP_201_CREATED)
def create_role(body: CreateRoleRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> AdminRole:
    if session.scalar(select(Role).where(Role.unit_id == context.unit_id, Role.code == body.code)) is not None:
        raise HTTPException(status_code=409, detail="角色编码已存在")
    role = Role(id=new_id(), code=body.code, name=body.name, scope_type=body.scope_type, unit_id=context.unit_id, built_in=False, status="active")
    session.add(role); session.commit()
    return AdminRole.model_validate(role, from_attributes=True)


@router.post("/roles/{role_id}/status", response_model=AdminRole)
def set_role_status(role_id: str, body: RoleStatusRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> AdminRole:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在或不属于当前单位")
    if role.built_in:
        raise HTTPException(status_code=409, detail="内置角色不可停用")
    role.status = body.status; session.commit()
    return AdminRole.model_validate(role, from_attributes=True)


@router.post("/roles/{role_id}/permissions", status_code=status.HTTP_201_CREATED)
def grant_permission(role_id: str, body: GrantPermissionRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> dict[str, str]:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    permission = session.scalar(select(Permission).where(Permission.code == body.permission_code, Permission.status == "active"))
    if role is None or permission is None:
        raise HTTPException(status_code=404, detail="角色或权限不存在")
    if body.data_scope == "project" and role.scope_type != "project":
        raise HTTPException(status_code=422, detail="项目范围权限只能授予项目角色")
    existing = session.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_code == permission.code, RolePermission.data_scope == body.data_scope))
    if existing is not None:
        raise HTTPException(status_code=409, detail="权限已授予")
    session.add(RolePermission(id=new_id(), role_id=role.id, permission_code=permission.code, unit_id=context.unit_id, data_scope=body.data_scope)); session.commit()
    return {"role_id": role.id, "permission_code": permission.code, "data_scope": body.data_scope}


@router.get("/permissions", response_model=list[AdminPermission])
def list_permissions(
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminPermission]:
    rows = session.scalars(select(Permission).order_by(Permission.resource, Permission.action, Permission.code)).all()
    return [AdminPermission.model_validate(permission, from_attributes=True) for permission in rows]
