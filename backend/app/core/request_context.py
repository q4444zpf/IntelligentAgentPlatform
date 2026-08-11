from datetime import datetime, timezone
from typing import Annotated, Literal, TypeAlias

from fastapi import Cookie, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session

UserRole: TypeAlias = Literal["user", "project_admin", "unit_admin", "unit_auditor"]
VALID_ROLES = frozenset({"user", "project_admin", "unit_admin", "unit_auditor"})

from app.identity.catalogue import ROLE_PERMISSION_CODES
from app.identity.schemas import AuthorizationContext, PermissionGrant


class RequestContext(BaseModel):
    user_id: str
    project_id: str
    unit_id: str
    roles: frozenset[UserRole] = frozenset({"user"})

    @property
    def role(self) -> Literal["user", "admin"]:
        return "admin" if self.roles & {"project_admin", "unit_admin"} else "user"

    @property
    def role_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.roles))


def require_request_context(
    request: Request,
    session_cookie: Annotated[str | None, Cookie(alias="iap_session")] = None,
    session: Session = Depends(get_session),
    dev_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    dev_project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
    dev_unit_id: Annotated[str | None, Header(alias="X-Unit-ID")] = None,
    dev_roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
    dev_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> RequestContext:
    if session_cookie:
        return _cookie_request_context(session, session_cookie)
    from .settings import settings
    client_host = (request.client.host if request.client else "").lower()
    loopback_hosts = {"127.0.0.1", "localhost", "::1", "testserver", "testclient"}
    if (
        not getattr(request.app.state, "allow_dev_identity", False)
        or settings.environment not in {"development", "test"}
        or client_host not in loopback_hosts
    ):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not dev_user_id or not dev_project_id or not dev_unit_id:
        raise HTTPException(
            status_code=401,
            detail="Unit, user, and project headers are required",
        )
    if dev_role not in {None, "user", "admin", *VALID_ROLES}:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    parsed_roles = {
        value.strip() for value in dev_roles.split(",") if value.strip()
    } if dev_roles is not None else set()
    if not parsed_roles and dev_role is not None:
        parsed_roles = {"project_admin" if dev_role == "admin" else dev_role}
    if not parsed_roles:
        parsed_roles = {"user"}
    if not parsed_roles <= VALID_ROLES:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    return RequestContext(
        unit_id=dev_unit_id,
        user_id=dev_user_id,
        project_id=dev_project_id,
        roles=frozenset(parsed_roles),
    )


def _cookie_request_context(session: Session, session_cookie: str) -> RequestContext:
    """Build a request context from the server-side session, never request headers."""
    import hashlib
    from app.identity.models import AuthSession, LocalCredential, UnitMembership, User
    from app.identity.repository import AuthorizationRepository

    auth = session.scalar(
        select(AuthSession).where(
            AuthSession.session_token_hash == hashlib.sha256(session_cookie.encode()).hexdigest(),
            AuthSession.revoked_at.is_(None),
        )
    )
    now = datetime.now(timezone.utc)
    if auth is None:
        raise HTTPException(status_code=401, detail="Session is invalid")
    user = session.get(User, auth.user_id)
    membership = session.scalar(select(UnitMembership).where(
        UnitMembership.user_id == auth.user_id,
        UnitMembership.unit_id == auth.unit_id,
        UnitMembership.status == "active",
    ))
    if (
        user is None or user.status != "active" or membership is None
        or (auth.idle_expires_at and auth.idle_expires_at.replace(tzinfo=timezone.utc) <= now)
        or (auth.absolute_expires_at and auth.absolute_expires_at.replace(tzinfo=timezone.utc) <= now)
        or auth.authorization_version != user.authorization_version
    ):
        raise HTTPException(status_code=401, detail="Session is invalid")
    credential = session.get(LocalCredential, user.id)
    if credential is not None and credential.must_change_password:
        raise HTTPException(status_code=403, detail="PASSWORD_CHANGE_REQUIRED")
    try:
        authorization = AuthorizationRepository(session).load_context(auth.id)
    except LookupError as error:
        raise HTTPException(status_code=401, detail="Session is invalid") from error
    roles = frozenset(code for code in authorization.role_codes if code in VALID_ROLES)
    if not roles:
        roles = frozenset({"user"})
    return RequestContext(
        user_id=authorization.user_id,
        project_id=authorization.current_project_id or "",
        unit_id=authorization.unit_id,
        roles=roles,
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
