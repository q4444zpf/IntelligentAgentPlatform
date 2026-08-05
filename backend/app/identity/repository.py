from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AuthSession,
    Permission,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    RolePermission,
    RolePermissionProject,
    UnitMembership,
    UnitMembershipRole,
    User,
)
from .schemas import AuthorizationContext, PermissionGrant


class AuthorizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_context(self, session_id: str) -> AuthorizationContext:
        auth = self.session.scalar(select(AuthSession).where(AuthSession.id == session_id))
        if auth is None or auth.revoked_at is not None:
            raise LookupError("authorization session not found")
        user = self.session.scalar(select(User).where(User.id == auth.user_id))
        membership = self.session.scalar(
            select(UnitMembership).where(
                UnitMembership.user_id == auth.user_id,
                UnitMembership.unit_id == auth.unit_id,
                UnitMembership.status == "active",
            )
        )
        if user is None or user.status != "active" or membership is None:
            raise LookupError("authorization membership is inactive")

        project_id = self._valid_current_project(auth)
        unit_roles = self.session.execute(
            select(Role, RolePermission).join(
                UnitMembershipRole, UnitMembershipRole.role_id == Role.id
            ).join(RolePermission, RolePermission.role_id == Role.id).join(
                Permission, Permission.code == RolePermission.permission_code,
            ).where(
                UnitMembershipRole.user_id == auth.user_id,
                UnitMembershipRole.unit_id == auth.unit_id,
                Role.unit_id == auth.unit_id,
                Permission.status == "active",
                Role.status == "active",
            )
        ).all()
        project_roles = self.session.execute(
            select(Role, RolePermission).join(
                ProjectMembershipRole, ProjectMembershipRole.role_id == Role.id
            ).join(RolePermission, RolePermission.role_id == Role.id).join(
                Permission, Permission.code == RolePermission.permission_code,
            ).join(
                ProjectMembership,
                (ProjectMembership.user_id == ProjectMembershipRole.user_id)
                & (ProjectMembership.unit_id == ProjectMembershipRole.unit_id)
                & (ProjectMembership.project_id == ProjectMembershipRole.project_id),
            ).where(
                ProjectMembershipRole.user_id == auth.user_id,
                ProjectMembershipRole.unit_id == auth.unit_id,
                Role.unit_id == auth.unit_id,
                ProjectMembership.status == "active",
                Role.status == "active",
            )
        ).all()
        # The explicit rows are assembled below to keep the scope boundary visible.
        grants: dict[tuple[str, str, frozenset[str], str | None], PermissionGrant] = {}
        role_codes: set[str] = set()
        for role, permission in [*unit_roles, *project_roles]:
            role_codes.add(role.code)
            projects = self._role_projects(role.id, auth.user_id, auth.unit_id)
            custom = frozenset(self.session.scalars(
                select(RolePermissionProject.project_id).join(
                    Project, Project.id == RolePermissionProject.project_id,
                ).where(
                    RolePermissionProject.role_permission_id == permission.id,
                    RolePermissionProject.unit_id == auth.unit_id,
                    Project.status == "active",
                )
            ))
            ids = custom or projects
            owner = auth.user_id if permission.data_scope == "own" else None
            grant = PermissionGrant(permission.permission_code, permission.data_scope, frozenset(ids), owner)
            grants[(grant.permission_code, grant.data_scope, grant.project_ids, grant.owner_user_id)] = grant
        return AuthorizationContext(
            session_id=auth.id,
            user_id=auth.user_id,
            unit_id=auth.unit_id,
            current_project_id=project_id,
            auth_method=auth.auth_method,
            authorization_version=auth.authorization_version,
            role_codes=tuple(sorted(role_codes)),
            grants=tuple(sorted(grants.values(), key=lambda item: (item.permission_code, item.data_scope))),
        )

    def _role_projects(self, role_id: str, user_id: str, unit_id: str) -> frozenset[str]:
        return frozenset(self.session.scalars(
            select(ProjectMembershipRole.project_id).join(
                ProjectMembership,
                (ProjectMembership.user_id == ProjectMembershipRole.user_id)
                & (ProjectMembership.unit_id == ProjectMembershipRole.unit_id)
                & (ProjectMembership.project_id == ProjectMembershipRole.project_id),
            ).join(Project, Project.id == ProjectMembership.project_id).where(
                ProjectMembershipRole.role_id == role_id,
                ProjectMembershipRole.user_id == user_id,
                ProjectMembershipRole.unit_id == unit_id,
                ProjectMembership.status == "active",
                Project.status == "active",
            )
        ))

    def _valid_current_project(self, auth: AuthSession) -> str | None:
        if auth.current_project_id is None:
            return None
        return self.session.scalar(select(Project.id).join(
            ProjectMembership,
            (ProjectMembership.project_id == Project.id)
            & (ProjectMembership.unit_id == Project.unit_id),
        ).where(
            Project.id == auth.current_project_id,
            Project.unit_id == auth.unit_id,
            Project.status == "active",
            ProjectMembership.user_id == auth.user_id,
            ProjectMembership.status == "active",
        ))
