from typing import Annotated, Literal, TypeAlias

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel

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
