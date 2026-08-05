import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.settings import settings

from .models import AuthSession, Project, UnitMembership, User, new_id

router = APIRouter()
SESSION_COOKIE = "iap_session"

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

@router.get("/login")
def oidc_login() -> dict[str, str]:
    if not settings.oidc_issuer or not settings.oidc_client_id or not settings.oidc_redirect_uri:
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    state = secrets.token_urlsafe(32)
    return {"status": "oidc_configuration_ready", "issuer": settings.oidc_issuer, "client_id": settings.oidc_client_id, "state": state}

@router.get("/callback")
def oidc_callback() -> dict[str, str]:
    raise HTTPException(status_code=501, detail="OIDC provider callback exchange is not enabled in this environment")

def _dev_identity(
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    unit_id: Annotated[str | None, Header(alias="X-Unit-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
) -> tuple[str, str, str]:
    if not settings.allow_dev_identity or settings.environment == "production":
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not unit_id or not project_id:
        raise HTTPException(status_code=401, detail="Development identity headers are required")
    return user_id, unit_id, project_id

@router.post("/dev/login")
def dev_login(
    response: Response,
    identity: Annotated[tuple[str, str, str], Depends(_dev_identity)],
    session: Session = Depends(get_session),
) -> dict[str, str]:
    user_id, unit_id, project_id = identity
    user = session.scalar(select(User).where(User.id == user_id, User.status == "active"))
    membership = session.scalar(select(UnitMembership).where(UnitMembership.user_id == user_id, UnitMembership.unit_id == unit_id, UnitMembership.status == "active"))
    project = session.scalar(select(Project).where(Project.id == project_id, Project.unit_id == unit_id, Project.status == "active"))
    if user is None or membership is None or project is None:
        raise HTTPException(status_code=403, detail="Development identity is not provisioned")
    token = secrets.token_urlsafe(32); now = datetime.now(timezone.utc)
    session.add(AuthSession(id=new_id(), session_token_hash=_hash(token), user_id=user_id, unit_id=unit_id, current_project_id=project_id, auth_method="dev_test", csrf_secret_encrypted={"ciphertext": secrets.token_urlsafe(24)}, provider_tokens_encrypted=None, provider_sid=None, authorization_version=user.authorization_version, idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=8), last_seen_at=now))
    session.commit(); response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=settings.session_cookie_secure, max_age=28800, path="/")
    return {"status": "ok", "auth_method": "dev_test"}

@router.get("/me")
def auth_me(session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None, session: Session = Depends(get_session)) -> dict:
    if not session_cookie:
        raise HTTPException(status_code=401, detail="Authentication is required")
    auth = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash(session_cookie), AuthSession.revoked_at.is_(None)))
    if auth is None:
        raise HTTPException(status_code=401, detail="Session is invalid")
    user = session.scalar(select(User).where(User.id == auth.user_id))
    if user is None or user.status != "active" or user.authorization_version != auth.authorization_version:
        raise HTTPException(status_code=401, detail="Session authorization is stale")
    return {"user": {"id": user.id, "display_name": user.display_name}, "unit_id": auth.unit_id, "current_project_id": auth.current_project_id, "auth_method": auth.auth_method, "authorization_version": user.authorization_version}

@router.post("/logout")
def logout(response: Response, session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None, session: Session = Depends(get_session)) -> dict[str, str]:
    if session_cookie:
        auth = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash(session_cookie), AuthSession.revoked_at.is_(None)))
        if auth is not None:
            auth.revoked_at = datetime.now(timezone.utc); auth.revoke_reason = "logout"; session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
