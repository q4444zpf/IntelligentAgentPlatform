import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url

REQUIRED_ENVIRONMENT = (
    "IAP_PROJECT_SKILLS_API_ENABLED",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "TEST_S3_ENDPOINT",
    "TEST_S3_ACCESS_KEY",
    "TEST_S3_SECRET_KEY",
)
if os.environ.get("IAP_PROJECT_SKILLS_API_ENABLED", "").lower() != "true" or not all(
    os.environ.get(name) for name in REQUIRED_ENVIRONMENT
):
    pytest.skip(
        "requires enabled Project Skill service PostgreSQL and MinIO configuration",
        allow_module_level=True,
    )

DATABASE_NAME = "iap_project_skill_activation_test_20260909_a"
assert make_url(os.environ["DATABASE_URL"]).database == DATABASE_NAME
assert make_url(os.environ["TEST_DATABASE_URL"]).database == DATABASE_NAME
assert not make_url(os.environ["TEST_DATABASE_URL"]).query

BUCKET = f"iap-project-skill-main-{uuid4().hex}"
os.environ["IAP_OBJECT_STORAGE_ENDPOINT"] = os.environ["TEST_S3_ENDPOINT"]
os.environ["IAP_OBJECT_STORAGE_ACCESS_KEY"] = os.environ["TEST_S3_ACCESS_KEY"]
os.environ["IAP_OBJECT_STORAGE_SECRET_KEY"] = os.environ["TEST_S3_SECRET_KEY"]
os.environ["IAP_OBJECT_STORAGE_REGION"] = os.environ.get("TEST_S3_REGION", "us-east-1")
os.environ["IAP_SKILL_BUCKET"] = BUCKET
os.environ["IAP_PUBLIC_BASE_URL"] = "https://platform.example"

from app.audit.models import AuditEvent
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    AuthSession,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    RolePermission,
    RolePermissionProject,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
)
from app.main import SessionFactory, app
from app.skills.models import Skill, SkillDraft
from app.skills.package_storage import (
    create_default_skill_package_storage,
    create_skill_storage_client,
    load_skill_storage_settings,
)
from app.skills.project_packages import package_from_content
from app.skills.repository import SkillRepository, SkillScope
from app.skills.storage_bootstrap import ensure_skill_bucket

PROJECT_METHODS = {
    "/api/project-skills": {"get", "post"},
    "/api/project-skills/import": {"post"},
    "/api/project-skills/{skill_id}": {"get"},
    "/api/project-skills/{skill_id}/draft": {"get", "put"},
    "/api/project-skills/{skill_id}/publish": {"post"},
    "/api/project-skills/{skill_id}/versions": {"get"},
    "/api/project-skills/{skill_id}/versions/{version_id}": {"get"},
}


def _manifest(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Production main acceptance for {name}\n"
        "version: '1.0'\n"
        "---\n"
        "Keep the acceptance boundary explicit.\n"
    )


@dataclass(frozen=True)
class MainAppEnvironment:
    unit_id: str
    user_id: str
    project_id: str
    foreign_project_id: str
    own_skill_id: str
    foreign_skill_id: str
    token: str
    csrf_token: str
    storage_client: object

    def object_keys(self) -> tuple[str, ...]:
        response = self.storage_client.list_objects_v2(Bucket=BUCKET)
        return tuple(sorted(item["Key"] for item in response.get("Contents", ())))

    def counts(self) -> tuple[int, int, int]:
        with SessionFactory() as session:
            skill_count = session.scalar(
                select(func.count())
                .select_from(Skill)
                .where(Skill.unit_id == self.unit_id)
            )
            audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.unit_id == self.unit_id)
            )
        return int(skill_count or 0), int(audit_count or 0), len(self.object_keys())


def _remove_fixture_rows(owned: dict[str, str]) -> None:
    with SessionFactory.begin() as session:
        assert session.scalar(text("SELECT current_database()")) == DATABASE_NAME
        skill_ids = tuple(
            session.scalars(select(Skill.id).where(Skill.unit_id == owned["unit_id"]))
        )
        if skill_ids:
            session.execute(
                delete(SkillDraft).where(SkillDraft.skill_id.in_(skill_ids))
            )
            session.execute(delete(Skill).where(Skill.id.in_(skill_ids)))
        session.execute(
            delete(AuditEvent).where(AuditEvent.unit_id == owned["unit_id"])
        )
        session.execute(
            delete(AuthSession).where(AuthSession.unit_id == owned["unit_id"])
        )
        session.execute(
            delete(RolePermissionProject).where(
                RolePermissionProject.unit_id == owned["unit_id"]
            )
        )
        session.execute(
            delete(ProjectMembershipRole).where(
                ProjectMembershipRole.unit_id == owned["unit_id"]
            )
        )
        session.execute(
            delete(UnitMembershipRole).where(
                UnitMembershipRole.unit_id == owned["unit_id"]
            )
        )
        session.execute(
            delete(RolePermission).where(RolePermission.unit_id == owned["unit_id"])
        )
        connection = session.connection()
        connection.execute(
            Role.__table__.delete().where(Role.__table__.c.unit_id == owned["unit_id"])
        )
        session.execute(
            delete(ProjectMembership).where(
                ProjectMembership.unit_id == owned["unit_id"]
            )
        )
        connection.execute(
            UnitMembership.__table__.delete().where(
                UnitMembership.__table__.c.unit_id == owned["unit_id"]
            )
        )
        session.execute(delete(Project).where(Project.unit_id == owned["unit_id"]))
        session.execute(delete(User).where(User.id == owned["user_id"]))
        session.execute(delete(Unit).where(Unit.id == owned["unit_id"]))


def _remove_fixture_bucket(storage_client: object, owned: dict[str, str]) -> None:
    environment = MainAppEnvironment(
        **owned,
        token="cleanup",
        csrf_token="cleanup",
        storage_client=storage_client,
    )
    try:
        for object_key in environment.object_keys():
            storage_client.delete_object(Bucket=BUCKET, Key=object_key)
        storage_client.delete_bucket(Bucket=BUCKET)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {
            "404",
            "NoSuchBucket",
        }:
            raise


@pytest.fixture(scope="module")
def real_main_environment():
    settings = load_skill_storage_settings()
    storage_client = create_skill_storage_client(settings)
    owned = {
        "unit_id": str(uuid4()),
        "user_id": str(uuid4()),
        "project_id": str(uuid4()),
        "foreign_project_id": str(uuid4()),
        "own_skill_id": str(uuid4()),
        "foreign_skill_id": str(uuid4()),
    }
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    now = datetime.now(UTC)

    with SessionFactory() as session:
        assert session.scalar(text("SELECT current_database()")) == DATABASE_NAME

    ensure_skill_bucket(storage_client, BUCKET, settings.region_name)
    try:
        with SessionFactory.begin() as session:
            session.add(
                Unit(
                    id=owned["unit_id"],
                    code=f"task6-{uuid4().hex}",
                    name="Task 6 acceptance unit",
                    status="active",
                )
            )
            session.add(
                User(
                    id=owned["user_id"],
                    display_name="Task 6 acceptance user",
                    status="active",
                    authorization_version=1,
                )
            )
            session.flush()
            session.add(
                UnitMembership(
                    id=str(uuid4()),
                    user_id=owned["user_id"],
                    unit_id=owned["unit_id"],
                    status="active",
                )
            )
            for project_id, suffix in (
                (owned["project_id"], "owned"),
                (owned["foreign_project_id"], "foreign"),
            ):
                session.add(
                    Project(
                        id=project_id,
                        unit_id=owned["unit_id"],
                        code=f"task6-{suffix}-{uuid4().hex}",
                        name=f"Task 6 {suffix} project",
                        status="active",
                    )
                )
            session.flush()
            session.add(
                ProjectMembership(
                    id=str(uuid4()),
                    user_id=owned["user_id"],
                    unit_id=owned["unit_id"],
                    project_id=owned["project_id"],
                    status="active",
                )
            )
            seed_builtin_catalogue(session, owned["unit_id"])
            session.flush()
            project_admin = session.scalar(
                select(Role).where(
                    Role.unit_id == owned["unit_id"],
                    Role.code == "project_admin",
                )
            )
            assert project_admin is not None
            session.add(
                ProjectMembershipRole(
                    id=str(uuid4()),
                    user_id=owned["user_id"],
                    unit_id=owned["unit_id"],
                    project_id=owned["project_id"],
                    role_id=project_admin.id,
                    scope_type="project",
                )
            )
            session.add(
                AuthSession(
                    id=str(uuid4()),
                    session_token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=owned["user_id"],
                    unit_id=owned["unit_id"],
                    current_project_id=owned["project_id"],
                    auth_method="oidc",
                    csrf_secret_encrypted={"ciphertext": csrf_token},
                    provider_tokens_encrypted=None,
                    provider_sid=None,
                    authorization_version=1,
                    idle_expires_at=now + timedelta(minutes=30),
                    absolute_expires_at=now + timedelta(hours=8),
                    last_seen_at=now,
                )
            )

        storage = create_default_skill_package_storage()
        with SessionFactory.begin() as session:
            repository = SkillRepository(session)
            for project_id, skill_id, name in (
                (owned["project_id"], owned["own_skill_id"], "owned-skill"),
                (
                    owned["foreign_project_id"],
                    owned["foreign_skill_id"],
                    "foreign-skill",
                ),
            ):
                package = package_from_content(_manifest(name))
                stored = storage.put(owned["unit_id"], project_id, skill_id, package)
                repository.create(
                    SkillScope(owned["unit_id"], project_id),
                    skill_id=skill_id,
                    name=name,
                    created_by=owned["user_id"],
                    package=package,
                    stored=stored,
                )

        yield MainAppEnvironment(
            **owned,
            token=token,
            csrf_token=csrf_token,
            storage_client=storage_client,
        )
    finally:
        try:
            _remove_fixture_rows(owned)
        finally:
            try:
                _remove_fixture_bucket(storage_client, owned)
            finally:
                storage_client.close()


def test_enabled_production_main_exposes_complete_project_skill_routes(
    real_main_environment,
):
    del real_main_environment
    paths = app.openapi()["paths"]
    project_paths = {
        path: set(methods)
        for path, methods in paths.items()
        if path.startswith("/api/project-skills")
    }
    assert project_paths == PROJECT_METHODS
    assert "/api/skills" in paths


def test_production_main_enforces_cookie_scope_and_atomic_write_boundaries(
    real_main_environment,
):
    environment = real_main_environment
    baseline = environment.counts()
    assert baseline == (2, 0, 2)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/project-skills").status_code == 401
        client.cookies.set("iap_session", environment.token)

        listed = client.get("/api/project-skills")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [
            environment.own_skill_id
        ]

        missing_csrf = client.post(
            "/api/project-skills", json={"content": _manifest("blocked-skill")}
        )
        assert missing_csrf.status_code == 403
        assert environment.counts() == baseline

        foreign_origin = client.post(
            "/api/project-skills",
            json={"content": _manifest("foreign-origin-skill")},
            headers={
                "Origin": "https://foreign.example",
                "X-CSRF-Token": environment.csrf_token,
            },
        )
        assert foreign_origin.status_code == 403
        assert environment.counts() == baseline

        created = client.post(
            "/api/project-skills",
            json={"content": _manifest("accepted-skill")},
            headers={"X-CSRF-Token": environment.csrf_token},
        )
        assert created.status_code == 201, created.text
        created_id = created.json()["id"]
        object_keys = environment.object_keys()
        assert len(object_keys) == baseline[2] + 1
        assert any(
            key.startswith(
                f"{environment.unit_id}/{environment.project_id}/{created_id}/"
            )
            for key in object_keys
        )
        with SessionFactory() as session:
            audit = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.unit_id == environment.unit_id,
                    AuditEvent.project_id == environment.project_id,
                    AuditEvent.resource_id == created_id,
                    AuditEvent.action == "skill.create",
                    AuditEvent.status == "succeeded",
                )
            ).all()
            assert len(audit) == 1

        accepted = environment.counts()
        assert accepted == (baseline[0] + 1, baseline[1] + 1, baseline[2] + 1)
        surrogate = client.post(
            "/api/project-skills",
            content=b'{"content":"\\ud800"}',
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": environment.csrf_token,
            },
        )
        assert surrogate.status_code == 422
        assert environment.counts() == accepted
