from typing import Annotated, Literal, TypeAlias

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.identity.catalogue import ROLE_PERMISSION_CODES
from app.identity.schemas import AuthorizationContext, PermissionGrant

UserRole: TypeAlias = Literal["user", "project_admin", "unit_auditor"]
VALID_ROLES = frozenset({"user", "project_admin", "unit_auditor"})


class RequestContext(BaseModel):
    user_id: str
    project_id: str
    unit_id: str
    roles: frozenset[UserRole] = frozenset({"user"})

    @property
    def role(self) -> Literal["user", "admin"]:
        return "admin" if "project_admin" in self.roles else "user"

    @property
    def role_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.roles))


def require_request_context(
    request: Request,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
    unit_id: Annotated[str | None, Header(alias="X-Unit-ID")] = None,
    roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
    role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> RequestContext:
    if not getattr(request.app.state, "allow_dev_identity", False):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not project_id or not unit_id:
        raise HTTPException(
            status_code=401,
            detail="Unit, user, and project headers are required",
        )
    if role not in {None, "user", "admin"}:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    parsed_roles = {
        value.strip() for value in roles.split(",") if value.strip()
    } if roles is not None else set()
    if not parsed_roles and role is not None:
        parsed_roles = {"project_admin" if role == "admin" else role}
    if not parsed_roles:
        parsed_roles = {"user"}
    if not parsed_roles <= VALID_ROLES:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    return RequestContext(
        unit_id=unit_id,
        user_id=user_id,
        project_id=project_id,
        roles=frozenset(parsed_roles),
    )


def require_admin_context(
    context: RequestContext = Depends(require_request_context),
) -> RequestContext:
    if context.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator permission is required",
        )
    return context


def require_dev_authorization_context(
    request: Request,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
    unit_id: Annotated[str | None, Header(alias="X-Unit-ID")] = None,
    roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
) -> AuthorizationContext:
    """Build a test-only authorization snapshot from development headers."""
    if not getattr(request.app.state, "allow_dev_identity", False):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not unit_id:
        raise HTTPException(status_code=401, detail="Unit and user headers are required")
    parsed = tuple(sorted({item.strip() for item in (roles or "viewer").split(",") if item.strip()}))
    allowed = {"user", *ROLE_PERMISSION_CODES}
    if not parsed or not set(parsed) <= allowed:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    grants: list[PermissionGrant] = []
    for role in parsed:
        catalogue_role = "viewer" if role == "user" else role
        if catalogue_role in ROLE_PERMISSION_CODES:
            scope = "unit" if catalogue_role == "unit_auditor" else "project"
            project_ids = frozenset({project_id}) if scope == "project" and project_id else frozenset()
            for code in ROLE_PERMISSION_CODES[catalogue_role]:
                grants.append(PermissionGrant(code, scope, project_ids, None))
    return AuthorizationContext(
        session_id="dev-test",
        user_id=user_id,
        unit_id=unit_id,
        current_project_id=project_id,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=parsed,
        grants=tuple(grants),
    )
