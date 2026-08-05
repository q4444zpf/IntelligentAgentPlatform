from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

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
