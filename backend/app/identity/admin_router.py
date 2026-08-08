from typing import Annotated
import secrets
import string

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.audit.recorder import AuditRecordRequest, AuditRecorder

from .models import (
    AuthSession, ExternalIdentity, LocalCredential, Permission,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    RolePermission,
    RolePermissionProject,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
    new_id,
)
from .schemas import (
    AdminPermission,
    AdminRolePermission,
    AdminProject,
    AdminProjectMembership,
    AdminRole,
    AdminRoleSummary,
    AdminUnit,
    AdminUser,
    AssignIdentityRoleRequest, BindExternalIdentityRequest, CreateIdentityUserRequest,
    IdentityStatusRequest, PasswordResetRequest, UpdateIdentityUserRequest,
    ReplaceIdentityRolesRequest,
    CreateProjectRequest, UpdateProjectRequest, ProjectStatusRequest,
    CreateRoleRequest, RoleStatusRequest, GrantPermissionRequest,
)
from .passwords import hash_password
from .session_lifecycle import revoke_user_sessions

router = APIRouter()


def identity_admin_context(
    context: RequestContext = Depends(require_request_context),
) -> RequestContext:
    roles = set(context.roles)
    if not roles & {"unit_admin", "project_admin"}:
        raise HTTPException(status_code=403, detail="Administrator permission is required")
    return context


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


def _invalidate_role_users(session: Session, role_id: str, unit_id: str, reason: str) -> None:
    user_ids = set(session.scalars(select(UnitMembershipRole.user_id).where(
        UnitMembershipRole.role_id == role_id,
        UnitMembershipRole.unit_id == unit_id,
    )))
    user_ids.update(session.scalars(select(ProjectMembershipRole.user_id).where(
        ProjectMembershipRole.role_id == role_id,
        ProjectMembershipRole.unit_id == unit_id,
    )))
    for user_id in user_ids:
        user = session.get(User, user_id)
        if user is not None:
            _bump(user)
            revoke_user_sessions(session, user.id, reason)


def _is_oidc_bound(session: Session, user_id: str) -> bool:
    return session.scalar(
        select(ExternalIdentity.id).where(ExternalIdentity.user_id == user_id)
    ) is not None


def _normalized_email(email: str | None) -> str | None:
    value = email.strip().lower() if email is not None else None
    return value or None


def _ensure_email_available(session: Session, email: str | None, *, exclude_user_id: str | None = None) -> str | None:
    normalized = _normalized_email(email)
    if normalized is None:
        return None
    query = select(User.id).where(func.lower(User.email) == normalized)
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    if session.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="邮箱已存在")
    return normalized

def _ensure_display_name_available(session: Session, display_name: str, *, exclude_user_id: str | None = None) -> str:
    normalized = display_name.strip()
    query = select(User.id).where(func.lower(User.display_name) == normalized.lower())
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    if session.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    return normalized

def _generate_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    chars = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase), secrets.choice(string.digits), secrets.choice("!@#$%^&*")]
    return ''.join(chars + [secrets.choice(alphabet) for _ in range(12)])


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
    if body.initial_password is not None and body.invite is True:
        raise HTTPException(status_code=422, detail="初始密码和邀请状态不能同时设置")
    display_name = _ensure_display_name_available(session, body.display_name)
    email = _ensure_email_available(session, body.email)
    project = _ensure_project(session, body.project_id, context.unit_id)
    user = User(id=new_id(), display_name=display_name, email=email, status="active", authorization_version=1)
    session.add(user)
    session.flush()
    session.add(UnitMembership(id=new_id(), user_id=user.id, unit_id=context.unit_id, status="active"))
    if project is not None:
        session.add(ProjectMembership(id=new_id(), user_id=user.id, unit_id=context.unit_id, project_id=project.id, status="active"))
    invitation_status = "pending"
    if body.initial_password is not None:
        now = datetime.now(timezone.utc)
        session.add(LocalCredential(
            user_id=user.id,
            password_hash=hash_password(body.initial_password),
            password_changed_at=now,
            must_change_password=True,
            failed_attempts=0,
            locked_until=None,
        ))
        invitation_status = "not_required"
    session.commit()
    return AdminUser.from_row(
        user,
        "active",
        initial_password=body.initial_password,
        invitation_status=invitation_status,
    )


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
    user.display_name = _ensure_display_name_available(session, body.display_name, exclude_user_id=user.id)
    user.email = _ensure_email_available(session, body.email, exclude_user_id=user.id)
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


@router.post("/users/{user_id}/password-reset")
def reset_user_password(
    body: PasswordResetRequest,
    user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str | bool]:
    membership = _unit_member(session, user_id, context.unit_id)
    user = session.scalar(select(User).where(User.id == user_id))
    if membership is None or user is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    if _is_oidc_bound(session, user.id):
        raise HTTPException(status_code=409, detail="OIDC users do not have local passwords")
    credential = session.get(LocalCredential, user.id)
    now = datetime.now(timezone.utc)
    if credential is None:
        credential = LocalCredential(
            user_id=user.id,
            password_hash=hash_password(body.new_password),
            password_changed_at=now,
            must_change_password=True,
            failed_attempts=0,
            locked_until=None,
        )
        session.add(credential)
    else:
        credential.password_hash = hash_password(body.new_password)
        credential.password_changed_at = now
        credential.must_change_password = True
        credential.failed_attempts = 0
        credential.locked_until = None
    _bump(user)
    AuditRecorder().record(
        session,
        AuditRecordRequest(
            unit_id=context.unit_id,
            project_id=None,
            user_id=context.user_id,
            actor_roles=context.role_codes,
            authorization_scope="unit",
            event_scope="unit",
            auth_method=None,
            category="security",
            source="auth",
            action="auth.password.reset",
            status="succeeded",
            risk_level="high",
            resource_type="user",
            resource_id=user.id,
            resource_name=user.display_name,
            summary="Administrator reset a local user password",
            metadata={"target_user_id": user.id},
            allowed_metadata_keys=frozenset({"target_user_id"}),
            idempotency_key=f"identity-password-reset:{user.id}:{now.isoformat()}",
            occurred_at=now,
        ),
    )
    revoke_user_sessions(session, user.id, "password_reset", now=now)
    session.commit()
    return {"user_id": user.id, "must_change_password": True}

@router.post("/users/{user_id}/password-generate")
def generate_user_password(user_id: str, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> dict[str, str | bool]:
    password = _generate_password()
    result = reset_user_password(PasswordResetRequest(new_password=password), user_id, context, session)
    result["generated_password"] = password
    return result

@router.delete("/users/{user_id}")
def delete_user(user_id: str, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> dict[str, str | bool]:
    if user_id == context.user_id:
        raise HTTPException(status_code=409, detail="不能删除当前登录用户")
    membership = _unit_member(session, user_id, context.unit_id)
    user = session.scalar(select(User).where(User.id == user_id))
    if membership is None or user is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    for model in (UnitMembershipRole, ProjectMembershipRole, ProjectMembership, LocalCredential, ExternalIdentity, AuthSession):
        session.execute(delete(model).where(model.user_id == user_id))
    session.delete(membership); session.delete(user); session.commit()
    return {"user_id": user_id, "deleted": True}


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
    role = session.scalar(select(Role).where(Role.id == body.role_id, Role.unit_id == context.unit_id))
    project = _ensure_project(session, body.project_id, context.unit_id)
    if membership is None or role is None:
        raise HTTPException(status_code=404, detail="用户或角色不存在或无权访问")
    changed = False
    if project is None:
        if role.scope_type != "unit":
            raise HTTPException(status_code=422, detail="该角色必须绑定项目")
        exists = session.scalar(select(UnitMembershipRole).where(UnitMembershipRole.user_id == user_id, UnitMembershipRole.unit_id == context.unit_id, UnitMembershipRole.role_id == role.id))
        if exists is None:
            session.add(UnitMembershipRole(id=new_id(), user_id=user_id, unit_id=context.unit_id, role_id=role.id, scope_type="unit"))
            changed = True
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
            changed = True
    user = session.scalar(select(User).where(User.id == user_id))
    if changed and user is not None:
        _bump(user)
        revoke_user_sessions(session, user.id, "role_changed")
    session.commit()
    return {"user_id": user_id, "role_id": role.id, "project_id": project.id if project else None}


@router.get("/users/{user_id}/roles", response_model=list[AdminRoleSummary])
def list_user_roles(
    user_id: str,
    project_id: str | None = None,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminRoleSummary]:
    membership = _unit_member(session, user_id, context.unit_id)
    if membership is None or session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    project = _ensure_project(session, project_id, context.unit_id)
    if project is None:
        rows = session.execute(
            select(Role).join(UnitMembershipRole, UnitMembershipRole.role_id == Role.id).where(
                UnitMembershipRole.user_id == user_id,
                UnitMembershipRole.unit_id == context.unit_id,
            ).order_by(Role.name, Role.id)
        ).scalars().all()
        return [AdminRoleSummary(role_id=r.id, code=r.code, name=r.name, scope_type=r.scope_type) for r in rows]
    rows = session.execute(
        select(Role).join(ProjectMembershipRole, ProjectMembershipRole.role_id == Role.id).where(
            ProjectMembershipRole.user_id == user_id,
            ProjectMembershipRole.unit_id == context.unit_id,
            ProjectMembershipRole.project_id == project.id,
        ).order_by(Role.name, Role.id)
    ).scalars().all()
    return [AdminRoleSummary(role_id=r.id, code=r.code, name=r.name, scope_type=r.scope_type, project_id=project.id) for r in rows]


@router.delete("/users/{user_id}/roles")
def remove_role(
    body: AssignIdentityRoleRequest,
    user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str | bool | None]:
    membership = _unit_member(session, user_id, context.unit_id)
    role = session.scalar(select(Role).where(Role.id == body.role_id, Role.unit_id == context.unit_id))
    project = _ensure_project(session, body.project_id, context.unit_id)
    if membership is None or role is None:
        raise HTTPException(status_code=404, detail="用户或角色不存在或无权访问")
    if project is None and role.scope_type != "unit":
        raise HTTPException(status_code=422, detail="该角色必须绑定项目")
    if project is not None and role.scope_type != "project":
        raise HTTPException(status_code=422, detail="单位角色不能绑定单个项目")
    if project is None:
        binding = session.scalar(select(UnitMembershipRole).where(UnitMembershipRole.user_id == user_id, UnitMembershipRole.unit_id == context.unit_id, UnitMembershipRole.role_id == role.id))
    else:
        binding = session.scalar(select(ProjectMembershipRole).where(ProjectMembershipRole.user_id == user_id, ProjectMembershipRole.unit_id == context.unit_id, ProjectMembershipRole.project_id == project.id, ProjectMembershipRole.role_id == role.id))
    if binding is None:
        return {"user_id": user_id, "role_id": role.id, "project_id": project.id if project else None, "removed": False}
    session.delete(binding)
    user = session.get(User, user_id)
    if user is not None:
        _bump(user)
        revoke_user_sessions(session, user.id, "role_changed")
    session.commit()
    return {"user_id": user_id, "role_id": role.id, "project_id": project.id if project else None, "removed": True}


@router.put("/users/{user_id}/roles", response_model=list[AdminRoleSummary])
def replace_roles(
    body: ReplaceIdentityRolesRequest,
    user_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminRoleSummary]:
    membership = _unit_member(session, user_id, context.unit_id)
    if membership is None or session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在或不属于当前单位")
    project = _ensure_project(session, body.project_id, context.unit_id)
    role_ids = list(dict.fromkeys(body.role_ids))
    roles = session.scalars(select(Role).where(Role.id.in_(role_ids), Role.unit_id == context.unit_id)).all() if role_ids else []
    if len(roles) != len(role_ids):
        raise HTTPException(status_code=404, detail="角色不存在或无权访问")
    expected_scope = "project" if project is not None else "unit"
    if any(role.scope_type != expected_scope for role in roles):
        raise HTTPException(status_code=422, detail="角色范围与目标不匹配")
    if project is None:
        existing = session.scalars(select(UnitMembershipRole).where(UnitMembershipRole.user_id == user_id, UnitMembershipRole.unit_id == context.unit_id)).all()
        existing_ids = {item.role_id for item in existing}
        desired_ids = set(role_ids)
        for item in existing:
            if item.role_id not in desired_ids:
                session.delete(item)
        for role in roles:
            if role.id not in existing_ids:
                session.add(UnitMembershipRole(id=new_id(), user_id=user_id, unit_id=context.unit_id, role_id=role.id, scope_type="unit"))
    else:
        pm = session.scalar(select(ProjectMembership).where(ProjectMembership.user_id == user_id, ProjectMembership.unit_id == context.unit_id, ProjectMembership.project_id == project.id))
        if pm is None:
            pm = ProjectMembership(id=new_id(), user_id=user_id, unit_id=context.unit_id, project_id=project.id, status="active")
            session.add(pm); session.flush()
        existing = session.scalars(select(ProjectMembershipRole).where(ProjectMembershipRole.user_id == user_id, ProjectMembershipRole.unit_id == context.unit_id, ProjectMembershipRole.project_id == project.id)).all()
        existing_ids = {item.role_id for item in existing}
        desired_ids = set(role_ids)
        for item in existing:
            if item.role_id not in desired_ids:
                session.delete(item)
        for role in roles:
            if role.id not in existing_ids:
                session.add(ProjectMembershipRole(id=new_id(), user_id=user_id, unit_id=context.unit_id, project_id=project.id, role_id=role.id, scope_type="project"))
    changed = existing_ids != set(role_ids) if 'existing_ids' in locals() else bool(role_ids)
    user = session.get(User, user_id)
    if changed and user is not None:
        _bump(user)
        revoke_user_sessions(session, user.id, "role_changed")
    session.commit()
    return [AdminRoleSummary(role_id=r.id, code=r.code, name=r.name, scope_type=r.scope_type, project_id=project.id if project else None) for r in roles]
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


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str | bool]:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在或不属于当前单位")
    if role.built_in:
        raise HTTPException(status_code=409, detail="内置角色不可删除")
    session.execute(delete(UnitMembershipRole).where(UnitMembershipRole.role_id == role.id, UnitMembershipRole.unit_id == context.unit_id))
    session.execute(delete(ProjectMembershipRole).where(ProjectMembershipRole.role_id == role.id, ProjectMembershipRole.unit_id == context.unit_id))
    role_permission_ids = session.scalars(select(RolePermission.id).where(RolePermission.role_id == role.id, RolePermission.unit_id == context.unit_id)).all()
    if role_permission_ids:
        session.execute(delete(RolePermissionProject).where(RolePermissionProject.role_permission_id.in_(role_permission_ids), RolePermissionProject.unit_id == context.unit_id))
    session.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.unit_id == context.unit_id))
    session.delete(role)
    session.commit()
    return {"role_id": role_id, "deleted": True}


@router.post("/roles/{role_id}/permissions", status_code=status.HTTP_201_CREATED)
def grant_permission(role_id: str, body: GrantPermissionRequest, context: RequestContext = Depends(identity_admin_context), session: Session = Depends(get_session)) -> dict[str, str]:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    permission = session.scalar(select(Permission).where(Permission.code == body.permission_code, Permission.status == "active"))
    if role is None or permission is None:
        raise HTTPException(status_code=404, detail="角色或权限不存在")
    if role.built_in:
        raise HTTPException(status_code=409, detail="内置角色权限不可变更")
    if body.data_scope == "project" and role.scope_type != "project":
        raise HTTPException(status_code=422, detail="项目范围权限只能授予项目角色")
    existing = session.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_code == permission.code, RolePermission.data_scope == body.data_scope))
    if existing is not None:
        raise HTTPException(status_code=409, detail="权限已授予")
    session.add(RolePermission(id=new_id(), role_id=role.id, permission_code=permission.code, unit_id=context.unit_id, data_scope=body.data_scope)); session.commit()
    return {"role_id": role.id, "permission_code": permission.code, "data_scope": body.data_scope}


@router.get("/roles/{role_id}/permissions", response_model=list[AdminRolePermission])
def list_role_permissions(
    role_id: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminRolePermission]:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在或不属于当前单位")
    grants = session.scalars(
        select(RolePermission)
        .where(RolePermission.role_id == role.id, RolePermission.unit_id == context.unit_id)
        .order_by(RolePermission.permission_code, RolePermission.data_scope, RolePermission.id)
    ).all()
    return [AdminRolePermission.model_validate(grant, from_attributes=True) for grant in grants]


@router.delete("/roles/{role_id}/permissions/{permission_code}")
def revoke_role_permission(
    role_id: str,
    permission_code: str,
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> dict[str, str | bool]:
    role = session.scalar(select(Role).where(Role.id == role_id, Role.unit_id == context.unit_id))
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在或不属于当前单位")
    if role.built_in:
        raise HTTPException(status_code=409, detail="内置角色权限不可变更")
    grant = session.scalar(select(RolePermission).where(
        RolePermission.role_id == role.id,
        RolePermission.unit_id == context.unit_id,
        RolePermission.permission_code == permission_code,
    ))
    if grant is None:
        raise HTTPException(status_code=404, detail="角色未授予该权限")
    now = datetime.now(timezone.utc)
    session.delete(grant)
    _invalidate_role_users(session, role.id, context.unit_id, "role_permission_changed")
    AuditRecorder().record(
        session,
        AuditRecordRequest(
            unit_id=context.unit_id,
            project_id=None,
            user_id=context.user_id,
            actor_roles=context.role_codes,
            authorization_scope="unit",
            event_scope="unit",
            auth_method=None,
            category="management",
            source="system",
            action="identity.role_permission.revoked",
            status="succeeded",
            risk_level="high",
            resource_type="role",
            resource_id=role.id,
            resource_name=role.name,
            summary="Revoked a permission from a role",
            metadata={"role_id": role.id, "permission_code": permission_code},
            allowed_metadata_keys=frozenset({"role_id", "permission_code"}),
            idempotency_key=f"identity-role-permission-revoked:{grant.id}:{now.isoformat()}",
            occurred_at=now,
        ),
    )
    session.commit()
    return {"role_id": role.id, "permission_code": permission_code, "removed": True}


@router.get("/permissions", response_model=list[AdminPermission])
def list_permissions(
    context: RequestContext = Depends(identity_admin_context),
    session: Session = Depends(get_session),
) -> list[AdminPermission]:
    rows = session.scalars(select(Permission).order_by(Permission.resource, Permission.action, Permission.code)).all()
    return [AdminPermission.model_validate(permission, from_attributes=True) for permission in rows]
