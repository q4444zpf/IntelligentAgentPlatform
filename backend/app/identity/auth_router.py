import hashlib
import secrets
import base64
import hashlib as _hashlib
from urllib.parse import urlencode
import httpx
from authlib.jose import jwt
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.settings import settings

from .authorization import AuthorizationService
from .models import AuthSession, ExternalIdentity, Menu, MenuPermission, OidcLoginTransaction, Project, UnitMembership, User, new_id

router = APIRouter()
SESSION_COOKIE = "iap_session"
OIDC_BROWSER_COOKIE = "iap_oidc_browser"

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

def _validate_nonce(claims: dict, expected_nonce_hash: str) -> None:
    if _hash(str(claims.get("nonce", ""))) != expected_nonce_hash:
        raise ValueError("OIDC nonce mismatch")


def _validate_client_claims(claims: dict, client_id: str) -> None:
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences or claims.get("azp") != client_id:
        raise ValueError("OIDC client claims are invalid")


def _validate_browser_correlation(raw_value: str | None, expected_hash: str) -> None:
    if not raw_value or _hash(raw_value) != expected_hash:
        raise ValueError("OIDC browser correlation mismatch")


def _csrf_token(auth: AuthSession) -> str:
    secret = str(auth.csrf_secret_encrypted.get("ciphertext", ""))
    return _hash(f"{auth.id}:{secret}")


def _visible_menus(session: Session, authz: AuthorizationService, context) -> list[dict]:
    rows = session.execute(
        select(Menu, MenuPermission.permission_code)
        .join(MenuPermission, MenuPermission.menu_id == Menu.id, isouter=True)
        .where(Menu.status == "active")
        .order_by(Menu.sort_order, Menu.node_key)
    ).all()
    permissions_by_menu: dict[str, set[str]] = {}
    menus: dict[str, Menu] = {}
    for menu, permission_code in rows:
        menus[menu.id] = menu
        if permission_code:
            permissions_by_menu.setdefault(menu.id, set()).add(permission_code)

    visible_ids: set[str] = set()
    for menu_id, menu in menus.items():
        if menu.kind != "route":
            continue
        if menu.requires_current_project and context.current_project_id is None:
            continue
        target = menu.visibility_target or "unit"
        if any(authz.allows_entry(context, code, target) for code in permissions_by_menu.get(menu_id, ())):
            visible_ids.add(menu_id)
            if menu.parent_id:
                visible_ids.add(menu.parent_id)

    return [
        {
            "id": menu.id,
            "node_key": menu.node_key,
            "kind": menu.kind,
            "route_key": menu.route_key,
            "parent_id": menu.parent_id,
            "title": menu.title,
            "sort_order": menu.sort_order,
        }
        for menu in menus.values()
        if menu.id in visible_ids
    ]

async def _oidc_metadata() -> dict:
    if not settings.oidc_issuer:
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.oidc_read_timeout_seconds, connect=settings.oidc_connect_timeout_seconds)) as client:
            response = await client.get(url); response.raise_for_status(); payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=503, detail="OIDC discovery failed") from error
    if payload.get("issuer") != settings.oidc_issuer:
        raise HTTPException(status_code=503, detail="OIDC issuer mismatch")
    return payload

async def _validate_id_token(token: str, metadata: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.oidc_read_timeout_seconds, connect=settings.oidc_connect_timeout_seconds)) as client:
            jwks = (await client.get(metadata["jwks_uri"])).json()
        claims = jwt.decode(token, jwks)
        claims.validate()
    except Exception as error:
        raise HTTPException(status_code=502, detail="OIDC ID token validation failed") from error
    if claims.get("iss") != settings.oidc_issuer:
        raise HTTPException(status_code=502, detail="OIDC ID token claims are invalid")
    try:
        _validate_client_claims(claims, settings.oidc_client_id or "")
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return dict(claims)

@router.get("/login")
async def oidc_login(response: Response, session: Session = Depends(get_session)) -> dict[str, str]:
    if not settings.oidc_issuer or not settings.oidc_client_id or not settings.oidc_redirect_uri:
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    metadata = await _oidc_metadata(); state = secrets.token_urlsafe(32); nonce = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(48); browser_token = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(_hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    session.add(OidcLoginTransaction(id=new_id(), state_hash=_hash(state), nonce_hash=_hash(nonce), browser_correlation_hash=_hash(browser_token), pkce_verifier_encrypted={"value": verifier}, issuer=settings.oidc_issuer, client_id=settings.oidc_client_id, redirect_uri=settings.oidc_redirect_uri, return_to="/dashboard", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
    session.commit()
    response.set_cookie(OIDC_BROWSER_COOKIE, browser_token, httponly=True, samesite="lax", secure=settings.session_cookie_secure, max_age=300, path="/")
    return {"status": "oidc_authorization_ready", "state": state, "nonce": nonce, "code_challenge": challenge, "authorization_url": metadata["authorization_endpoint"] + "?" + urlencode({"response_type": "code", "client_id": settings.oidc_client_id, "redirect_uri": settings.oidc_redirect_uri, "scope": settings.oidc_scope, "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"})}

@router.get("/callback")
async def oidc_callback(response: Response, code: Annotated[str | None, Query()] = None, state: Annotated[str | None, Query()] = None, browser_cookie: Annotated[str | None, Cookie(alias=OIDC_BROWSER_COOKIE)] = None, session: Session = Depends(get_session)) -> dict[str, str]:
    if not code or not state:
        raise HTTPException(status_code=400, detail="OIDC callback code and state are required")
    transaction = session.scalar(select(OidcLoginTransaction).where(OidcLoginTransaction.state_hash == _hash(state), OidcLoginTransaction.consumed_at.is_(None)))
    if transaction is None or _aware(transaction.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OIDC callback transaction is invalid")
    try:
        _validate_browser_correlation(browser_cookie, transaction.browser_correlation_hash)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    metadata = await _oidc_metadata()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.oidc_read_timeout_seconds, connect=settings.oidc_connect_timeout_seconds)) as client:
            token_response = await client.post(metadata["token_endpoint"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": transaction.redirect_uri, "client_id": transaction.client_id, "code_verifier": transaction.pkce_verifier_encrypted["value"]})
            token_response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="OIDC token exchange failed") from error
    token_payload = token_response.json(); id_token = token_payload.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="OIDC response did not contain an ID token")
    claims = await _validate_id_token(id_token, metadata)
    try:
        _validate_nonce(claims, transaction.nonce_hash)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    issuer, subject = claims.get("iss"), claims.get("sub")
    external = session.scalar(select(ExternalIdentity).where(ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject))
    if external is None:
        raise HTTPException(status_code=403, detail="External identity is not bound")
    user = session.scalar(select(User).where(User.id == external.user_id, User.status == "active"))
    membership = session.scalar(select(UnitMembership).where(UnitMembership.user_id == user.id, UnitMembership.status == "active")) if user else None
    project = session.scalar(select(Project).where(Project.unit_id == membership.unit_id, Project.status == "active").order_by(Project.created_at)) if membership else None
    if user is None or membership is None or project is None:
        raise HTTPException(status_code=403, detail="External identity has no active platform membership")
    raw = secrets.token_urlsafe(32); now = datetime.now(timezone.utc)
    session.add(AuthSession(id=new_id(), session_token_hash=_hash(raw), user_id=user.id, unit_id=membership.unit_id, current_project_id=project.id, auth_method="oidc", csrf_secret_encrypted={"ciphertext": secrets.token_urlsafe(24)}, provider_tokens_encrypted=None, provider_sid=claims.get("sid"), authorization_version=user.authorization_version, idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=8), last_seen_at=now))
    transaction.consumed_at = now; session.commit()
    if response is not None:
        response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", secure=settings.session_cookie_secure, max_age=28800, path="/")
        response.delete_cookie(OIDC_BROWSER_COOKIE, path="/")
    return {"status": "authenticated", "auth_method": "oidc", "user_id": user.id}

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
    now = datetime.now(timezone.utc)
    if _aware(auth.idle_expires_at) <= now or _aware(auth.absolute_expires_at) <= now:
        auth.revoked_at = now
        auth.revoke_reason = "expired"
        session.commit()
        raise HTTPException(status_code=401, detail="Session has expired")
    user = session.scalar(select(User).where(User.id == auth.user_id))
    if user is None or user.status != "active" or user.authorization_version != auth.authorization_version:
        raise HTTPException(status_code=401, detail="Session authorization is stale")
    auth.last_seen_at = now
    auth.idle_expires_at = min(now + timedelta(minutes=30), _aware(auth.absolute_expires_at))
    projects = session.scalars(
        select(Project)
        .where(Project.unit_id == auth.unit_id, Project.status == "active")
        .order_by(Project.name, Project.id)
    ).all()
    current_project = next(
        (project for project in projects if project.id == auth.current_project_id),
        None,
    )
    authz = AuthorizationService()
    context = authz.load_context(session, auth.id)
    capabilities = authz.entry_capabilities(context)
    menus = _visible_menus(session, authz, context)
    session.commit()
    return {
        "user": {"id": user.id, "display_name": user.display_name},
        "unit_id": auth.unit_id,
        "current_project_id": auth.current_project_id,
        "current_project": (
            {"id": current_project.id, "name": current_project.name}
            if current_project is not None else None
        ),
        "projects": [{"id": project.id, "name": project.name} for project in projects],
        "auth_method": auth.auth_method,
        "authorization_version": user.authorization_version,
        "roles": list(context.role_codes),
        "permissions": [
            {"code": capability.code, "target": capability.target}
            for capability in capabilities
        ],
        "menus": menus,
        "csrf_token": _csrf_token(auth),
        "session": {
            "idle_expires_at": auth.idle_expires_at,
            "absolute_expires_at": auth.absolute_expires_at,
        },
    }

@router.post("/logout")
def logout(response: Response, session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None, session: Session = Depends(get_session)) -> dict[str, str]:
    if session_cookie:
        auth = session.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash(session_cookie), AuthSession.revoked_at.is_(None)))
        if auth is not None:
            auth.revoked_at = datetime.now(timezone.utc); auth.revoke_reason = "logout"; session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}
