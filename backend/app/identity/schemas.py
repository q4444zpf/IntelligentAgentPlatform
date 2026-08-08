from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DataScope = Literal["unit", "assigned_projects", "project", "own", "custom_projects"]
PermissionTarget = Literal["unit", "current_project"]


@dataclass(frozen=True)
class PermissionGrant:
    permission_code: str
    data_scope: DataScope
    project_ids: frozenset[str]
    owner_user_id: str | None


@dataclass(frozen=True)
class ResourceScope:
    unit_id: str
    project_id: str | None
    owner_user_id: str | None


@dataclass(frozen=True, order=True)
class PermissionCapability:
    code: str
    target: PermissionTarget


class AuthorizationContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    user_id: str
    unit_id: str
    current_project_id: str | None
    auth_method: Literal["oidc", "dev_test", "local"]
    authorization_version: int
    role_codes: tuple[str, ...]
    grants: tuple[PermissionGrant, ...]


class AdminUser(BaseModel):
    id: str
    display_name: str
    email: str | None
    status: str
    membership_status: str
    # Only populated by the create response; never persisted or returned by list/update.
    initial_password: str | None = None
    invitation_status: Literal["pending", "not_required"] | None = None
    project_memberships: list["AdminProjectMembership"] = Field(default_factory=list)
    role_summaries: list["AdminRoleSummary"] = Field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        user,
        membership_status: str,
        project_memberships=(),
        role_summaries=(),
        *,
        initial_password: str | None = None,
        invitation_status: Literal["pending", "not_required"] | None = None,
    ):
        return cls(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            membership_status=membership_status,
            initial_password=initial_password,
            invitation_status=invitation_status,
            project_memberships=list(project_memberships),
            role_summaries=list(role_summaries),
        )


class AdminProjectMembership(BaseModel):
    project_id: str
    project_code: str
    project_name: str
    status: str


class AdminRoleSummary(BaseModel):
    role_id: str
    code: str
    name: str
    scope_type: str
    project_id: str | None = None


class AdminUnit(BaseModel):
    id: str
    code: str
    name: str
    status: str


class AdminProject(BaseModel):
    id: str
    unit_id: str
    code: str
    name: str
    status: str


class AdminRole(BaseModel):
    id: str
    code: str
    name: str
    scope_type: str
    unit_id: str | None
    built_in: bool
    status: str


class AdminPermission(BaseModel):
    id: str
    code: str
    resource: str
    action: str
    risk_level: str
    status: str


class AdminRolePermission(BaseModel):
    id: str
    role_id: str
    permission_code: str
    data_scope: DataScope


class CreateIdentityUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    project_id: str | None = None
    initial_password: str | None = Field(default=None, min_length=12, max_length=256)
    invite: bool | None = None


class UpdateIdentityUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)


class LocalLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class IdentityStatusRequest(BaseModel):
    status: Literal["active", "inactive"]


class BindExternalIdentityRequest(BaseModel):
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=255)


class AssignIdentityRoleRequest(BaseModel):
    role_id: str = Field(min_length=1, max_length=36)
    project_id: str | None = None


class ReplaceIdentityRolesRequest(BaseModel):
    role_ids: list[str] = Field(default_factory=list, max_length=64)
    project_id: str | None = None


class CreateProjectRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)


class UpdateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class ProjectStatusRequest(BaseModel):
    status: Literal["active", "inactive"]


class CreateRoleRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    scope_type: Literal["unit", "project"]


class RoleStatusRequest(BaseModel):
    status: Literal["active", "inactive"]


class GrantPermissionRequest(BaseModel):
    permission_code: str = Field(min_length=1, max_length=100)
    data_scope: DataScope
