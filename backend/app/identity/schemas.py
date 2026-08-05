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
    auth_method: Literal["oidc", "dev_test"]
    authorization_version: int
    role_codes: tuple[str, ...]
    grants: tuple[PermissionGrant, ...]


class AdminUser(BaseModel):
    id: str
    display_name: str
    email: str | None
    status: str
    membership_status: str
    project_memberships: list["AdminProjectMembership"] = Field(default_factory=list)
    role_summaries: list["AdminRoleSummary"] = Field(default_factory=list)

    @classmethod
    def from_row(cls, user, membership_status: str, project_memberships=(), role_summaries=()):
        return cls(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            membership_status=membership_status,
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
