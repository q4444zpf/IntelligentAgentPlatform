import hashlib
import io
import secrets
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import yaml
from app.core.request_context import RequestContext
from app.db.base import Base
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    AuthSession,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    Unit,
    UnitMembership,
    User,
)
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.skills.package import parse_skill_bundle
from app.skills.package_storage import SkillPackageStorage
from app.skills.repository import SkillRepository, SkillScope
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.skills.test_package_storage import MemoryS3


def make_context(
    *permissions: str,
    user_id: str = "user-1",
    unit_id: str = "unit-1",
    project_id: str = "project-1",
    data_scope: str = "project",
) -> RequestContext:
    project_ids = (
        frozenset()
        if data_scope == "unit" or not project_id
        else frozenset({project_id})
    )
    authorization = AuthorizationContext(
        session_id="skill-test-session",
        user_id=user_id,
        unit_id=unit_id,
        current_project_id=project_id or None,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("viewer",),
        grants=tuple(
            PermissionGrant(
                permission,
                data_scope,
                project_ids,
                None,
            )
            for permission in permissions
        ),
    )
    return RequestContext(
        user_id=user_id,
        unit_id=unit_id,
        project_id=project_id,
        authorization_context=authorization,
    )


def manifest(name: str, body: str = "Initial instructions") -> str:
    frontmatter = yaml.safe_dump(
        {"name": name, "description": f"Description for {name}", "version": "1.0"},
        sort_keys=False,
    )
    return f"---\n{frontmatter}---\n{body}\n"


def bundle(entries: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(path, data)
    return stream.getvalue()


def seed_skill(
    sessions: sessionmaker[Session],
    storage: SkillPackageStorage,
    context: RequestContext,
    *,
    name: str = "s",
    body: str = "Initial instructions",
    attachments: tuple[tuple[str, bytes], ...] = (),
) -> str:
    skill_id = str(uuid4())
    package = parse_skill_bundle(
        bundle(
            [
                (f"{name}/SKILL.md", manifest(name, body).encode()),
                *((f"{name}/{path}", data) for path, data in attachments),
            ]
        )
    )[0]
    stored = storage.put(context.unit_id, context.project_id, skill_id, package)
    with sessions() as session:
        SkillRepository(session).create(
            SkillScope(context.unit_id, context.project_id),
            skill_id=skill_id,
            name=name,
            created_by=context.user_id,
            package=package,
            stored=stored,
        )
        session.commit()
    return skill_id


def publish_skill(
    sessions: sessionmaker[Session],
    context: RequestContext,
    skill_id: str,
    *,
    expected_revision: int = 1,
) -> str:
    with sessions() as session:
        version = SkillRepository(session).publish(
            SkillScope(context.unit_id, context.project_id),
            skill_id,
            expected_revision=expected_revision,
            idempotency_key=secrets.token_urlsafe(12),
            published_by=context.user_id,
        )
        session.commit()
        return version.id


def make_test_app(
    sessions: sessionmaker[Session],
    storage_factory: Callable[[], SkillPackageStorage],
    *,
    context: RequestContext | None = None,
    with_write_protection: bool = False,
) -> FastAPI:
    from app.core.database import get_session
    from app.core.request_context import require_request_context
    from app.skills.project_router import _service, router
    from app.skills.project_service import ProjectSkillService

    app = FastAPI()
    app.state.allow_dev_identity = False
    app.include_router(router)

    def identity_session():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = identity_session
    app.dependency_overrides[_service] = lambda: ProjectSkillService(
        sessions, storage_factory=storage_factory
    )
    if context is not None:
        app.dependency_overrides[require_request_context] = lambda: context
    if with_write_protection:
        app.state.write_protection_enabled = True
    return app


def issue_session(
    sessions: sessionmaker[Session],
    *,
    user_id: str = "user-1",
    project_id: str = "project-1",
    role_code: str = "project_admin",
) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with sessions() as session:
        seed_builtin_catalogue(session, "unit-1")
        role = session.scalar(
            select(Role).where(Role.unit_id == "unit-1", Role.code == role_code)
        )
        assert role is not None
        user = session.get(User, user_id)
        assert user is not None
        membership = session.scalar(
            select(ProjectMembership).where(
                ProjectMembership.user_id == user_id,
                ProjectMembership.unit_id == "unit-1",
                ProjectMembership.project_id == project_id,
            )
        )
        if membership is None:
            session.add(
                ProjectMembership(
                    id=str(uuid4()),
                    user_id=user_id,
                    unit_id="unit-1",
                    project_id=project_id,
                    status="active",
                )
            )
            session.flush()
        else:
            membership.status = "active"
        binding = session.scalar(
            select(ProjectMembershipRole).where(
                ProjectMembershipRole.user_id == user_id,
                ProjectMembershipRole.unit_id == "unit-1",
                ProjectMembershipRole.project_id == project_id,
                ProjectMembershipRole.role_id == role.id,
            )
        )
        records = []
        if binding is None:
            records.append(
                ProjectMembershipRole(
                    id=str(uuid4()),
                    user_id=user_id,
                    unit_id="unit-1",
                    project_id=project_id,
                    role_id=role.id,
                    scope_type="project",
                )
            )
        records.append(
            AuthSession(
                id=str(uuid4()),
                session_token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user_id,
                unit_id="unit-1",
                current_project_id=project_id,
                auth_method="oidc",
                csrf_secret_encrypted={"ciphertext": "test"},
                provider_tokens_encrypted=None,
                provider_sid=None,
                authorization_version=user.authorization_version,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=8),
                last_seen_at=now,
            )
        )
        session.add_all(records)
        session.commit()
    return token


@pytest.fixture(name="sessions")
def sessions_fixture(tmp_path) -> Iterator[sessionmaker[Session]]:
    database_path = tmp_path / "project-skill-access.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        session.add_all(
            [
                Unit(id="unit-1", code="unit-1", name="Unit 1", status="active"),
                Unit(id="unit-2", code="unit-2", name="Unit 2", status="active"),
            ]
        )
        session.flush()
        session.add_all(
            [
                User(id="user-1", display_name="User 1", status="active"),
                User(id="user-2", display_name="User 2", status="active"),
                User(id="user-3", display_name="User 3", status="active"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Project(
                    id="project-1",
                    unit_id="unit-1",
                    code="project-1",
                    name="Project 1",
                    status="active",
                ),
                Project(
                    id="project-2",
                    unit_id="unit-1",
                    code="project-2",
                    name="Project 2",
                    status="active",
                ),
                Project(
                    id="project-inactive",
                    unit_id="unit-1",
                    code="project-inactive",
                    name="Inactive Project",
                    status="inactive",
                ),
                Project(
                    id="project-3",
                    unit_id="unit-2",
                    code="project-3",
                    name="Project 3",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                UnitMembership(
                    id="unit-membership-1",
                    user_id="user-1",
                    unit_id="unit-1",
                    status="active",
                ),
                UnitMembership(
                    id="unit-membership-2",
                    user_id="user-2",
                    unit_id="unit-1",
                    status="active",
                ),
                UnitMembership(
                    id="unit-membership-3",
                    user_id="user-3",
                    unit_id="unit-2",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMembership(
                    id="project-membership-1",
                    user_id="user-1",
                    unit_id="unit-1",
                    project_id="project-1",
                    status="active",
                ),
                ProjectMembership(
                    id="project-membership-2",
                    user_id="user-2",
                    unit_id="unit-1",
                    project_id="project-2",
                    status="active",
                ),
                ProjectMembership(
                    id="project-membership-3",
                    user_id="user-3",
                    unit_id="unit-2",
                    project_id="project-3",
                    status="active",
                ),
            ]
        )
        session.commit()

    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(name="memory_s3")
def memory_s3_fixture() -> MemoryS3:
    return MemoryS3()


@pytest.fixture(name="storage")
def storage_fixture(memory_s3: MemoryS3) -> SkillPackageStorage:
    return SkillPackageStorage(memory_s3, "project-skill-tests")
