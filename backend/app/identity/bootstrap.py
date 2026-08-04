import argparse
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    ExternalIdentity,
    Project,
    ProjectMembership,
    Role,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
    new_id,
)


@dataclass(frozen=True)
class BootstrapRequest:
    unit_code: str
    unit_name: str
    user_display_name: str
    issuer: str
    subject: str
    initial_project_code: str
    initial_project_name: str


_FIELD_LIMITS = {
    "unit_code": 64,
    "unit_name": 160,
    "user_display_name": 160,
    "issuer": 512,
    "subject": 255,
    "initial_project_code": 64,
    "initial_project_name": 160,
}


def _validate_request(request: BootstrapRequest) -> None:
    if not isinstance(request, BootstrapRequest):
        raise ValueError("request must be a BootstrapRequest")
    for field_name, max_length in _FIELD_LIMITS.items():
        value = getattr(request, field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} must be a non-empty string")
        if len(value) > max_length:
            raise ValueError(f"{field_name} exceeds {max_length} characters")


def bootstrap_initial_unit_admin(
    session: Session,
    request: BootstrapRequest,
) -> str:
    try:
        _validate_request(request)
        existing_identity = session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == request.issuer,
                ExternalIdentity.subject == request.subject,
            )
        )
        if existing_identity is not None:
            raise ValueError("external identity is already bound")

        now = datetime.now(timezone.utc)
        unit_id = new_id()
        project_id = new_id()
        user_id = new_id()
        unit = Unit(
            id=unit_id,
            code=request.unit_code,
            name=request.unit_name,
            status="active",
        )
        project = Project(
            id=project_id,
            unit_id=unit_id,
            code=request.initial_project_code,
            name=request.initial_project_name,
            status="active",
        )
        user = User(
            id=user_id,
            display_name=request.user_display_name,
            email=None,
            status="active",
            authorization_version=1,
        )
        session.add_all(
            [
                unit,
                project,
                user,
                ExternalIdentity(
                    id=new_id(),
                    user_id=user_id,
                    issuer=request.issuer,
                    subject=request.subject,
                    claims={},
                    last_login_at=now,
                ),
                UnitMembership(
                    id=new_id(),
                    user_id=user_id,
                    unit_id=unit_id,
                    status="active",
                ),
                ProjectMembership(
                    id=new_id(),
                    user_id=user_id,
                    unit_id=unit_id,
                    project_id=project_id,
                    status="active",
                ),
            ]
        )
        session.flush()

        seed_builtin_catalogue(session, unit_id)
        unit_admin = session.scalar(
            select(Role).where(
                Role.unit_id == unit_id,
                Role.code == "unit_admin",
                Role.scope_type == "unit",
                Role.built_in.is_(True),
            )
        )
        if unit_admin is None:
            raise RuntimeError("unit_admin role was not seeded")
        session.add(
            UnitMembershipRole(
                id=new_id(),
                user_id=user_id,
                unit_id=unit_id,
                role_id=unit_admin.id,
                scope_type="unit",
            )
        )
        user.authorization_version += 1

        AuditRecorder().record(
            session,
            AuditRecordRequest(
                unit_id=unit_id,
                project_id=None,
                user_id=user_id,
                actor_roles=("unit_admin",),
                authorization_scope="unit",
                event_scope="unit",
                auth_method="bootstrap",
                category="security",
                source="auth",
                action="auth.identity.bound",
                status="succeeded",
                risk_level="high",
                resource_type="user",
                resource_id=user_id,
                summary="Initial external identity bound.",
                metadata={"bootstrap": True},
                allowed_metadata_keys=frozenset({"bootstrap"}),
                idempotency_key=f"bootstrap:identity-bound:{user_id}",
                occurred_at=now,
            ),
        )
        session.commit()
        return user_id
    except SQLAlchemyError:
        session.rollback()
        raise RuntimeError("identity bootstrap failed") from None
    except Exception:
        session.rollback()
        raise


def _windows_acl_is_protected(path: Path) -> bool:
    script = """
$acl = [System.IO.File]::GetAccessControl($env:IAP_BOOTSTRAP_ACL_PATH)
$allowed = @($acl.GetAccessRules(
    $true,
    $true,
    [System.Security.Principal.SecurityIdentifier]
) | Where-Object {
    $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow
} | ForEach-Object {
    $_.IdentityReference.Value
})
[PSCustomObject]@{
    current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    owner = $acl.GetOwner(
        [System.Security.Principal.SecurityIdentifier]
    ).Value
    allowed = $allowed
} | ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment["IAP_BOOTSTRAP_ACL_PATH"] = str(path)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        acl = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    allowed = acl.get("allowed", [])
    if isinstance(allowed, str):
        allowed = [allowed]
    safe_owner_sids = {
        acl.get("current"),
        "S-1-5-18",       # Local System
        "S-1-5-32-544",  # Built-in Administrators
    }
    safe_allow_sids = {
        *safe_owner_sids,
        "S-1-3-4",        # Owner Rights
    }
    return (
        acl.get("owner") in safe_owner_sids
        and bool(allowed)
        and set(allowed) <= safe_allow_sids
    )


def _read_protected_json_file(path: Path) -> str:
    if path.suffix.casefold() != ".json":
        raise ValueError("bootstrap request must be a protected JSON file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ValueError("bootstrap request must be a protected JSON file")
        flags |= no_follow
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("bootstrap request must be a protected JSON file") from None

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("bootstrap request must be a protected JSON file")
        if os.name == "nt":
            if not _windows_acl_is_protected(path):
                raise ValueError("bootstrap request must be a protected JSON file")
            try:
                current_path = path.stat()
            except OSError:
                raise ValueError(
                    "bootstrap request must be a protected JSON file"
                ) from None
            if (opened.st_dev, opened.st_ino) != (
                current_path.st_dev,
                current_path.st_ino,
            ):
                raise ValueError("bootstrap request must be a protected JSON file")
        elif (
            stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_uid != os.geteuid()
        ):
            raise ValueError("bootstrap request must be a protected JSON file")

        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as request_file:
                descriptor = -1
                return request_file.read()
        except (OSError, UnicodeError):
            raise ValueError("unable to read bootstrap request JSON") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _is_protected_json_file(path: Path) -> bool:
    try:
        _read_protected_json_file(path)
    except ValueError:
        return False
    return True


def _request_from_json(path: Path) -> BootstrapRequest:
    try:
        payload = json.loads(_read_protected_json_file(path))
    except json.JSONDecodeError:
        raise ValueError("unable to read bootstrap request JSON") from None
    expected_fields = {field.name for field in fields(BootstrapRequest)}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("bootstrap request JSON fields do not match the contract")
    return BootstrapRequest(**payload)


def _request_from_prompt() -> BootstrapRequest:
    return BootstrapRequest(
        unit_code=input("Unit code: "),
        unit_name=input("Unit name: "),
        user_display_name=input("Initial administrator display name: "),
        issuer=input("OIDC issuer (preserved exactly): "),
        subject=input("OIDC subject (preserved exactly): "),
        initial_project_code=input("Initial project code: "),
        initial_project_name=input("Initial project name: "),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the first unit, project, and OIDC unit administrator.",
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        help="Protected JSON file; omit to enter each field interactively.",
    )
    arguments = parser.parse_args()
    request = (
        _request_from_json(arguments.request_file)
        if arguments.request_file is not None
        else _request_from_prompt()
    )
    from app.core.database import SessionFactory

    with SessionFactory() as session:
        user_id = bootstrap_initial_unit_admin(session, request)
    print(f"Created initial unit administrator {user_id}.")


if __name__ == "__main__":
    main()
