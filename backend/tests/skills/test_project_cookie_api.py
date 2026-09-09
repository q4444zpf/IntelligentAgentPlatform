import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    AuthSession,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    RolePermission,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.tests.skills.project_support import (
    issue_session,
    make_context,
    make_test_app,
    manifest,
    seed_skill,
)
from backend.tests.skills.project_support import (
    memory_s3_fixture as _memory_s3_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    sessions_fixture as _sessions_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    storage_fixture as _storage_fixture,  # noqa: F401
)


def test_cookie_writes_require_csrf_header_and_allow_present_header(sessions, storage):
    token = issue_session(sessions)
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    with TestClient(app) as client:
        client.cookies.set("iap_session", token)
        blocked = client.post("/api/project-skills", json={"content": manifest("s")})
        assert blocked.status_code == 403
        accepted = client.post(
            "/api/project-skills",
            json={"content": manifest("s")},
            headers={"X-CSRF-Token": "test-present"},
        )
        assert accepted.status_code == 201
        changed = client.put(
            f"/api/project-skills/{accepted.json()['id']}/draft",
            json={"expected_revision": 1, "content": manifest("s", "Updated")},
            headers={"X-CSRF-Token": "test-present"},
        )
        assert changed.status_code == 200
        assert changed.json()["revision"] == 2


def test_cookie_write_rejects_mismatched_origin(sessions, storage, monkeypatch):
    token = issue_session(sessions)
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    from app import main

    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, public_base_url="https://platform.example"),
    )
    with TestClient(app) as client:
        client.cookies.set("iap_session", token)
        response = client.post(
            "/api/project-skills",
            json={"content": manifest("s")},
            headers={"X-CSRF-Token": "test-present", "Origin": "https://other.example"},
        )
    assert response.status_code == 403


def test_cookie_write_ignores_forged_admin_and_project(sessions, storage, memory_s3):
    token = issue_session(sessions, role_code="viewer")
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    with TestClient(app) as client:
        client.cookies.set("iap_session", token)
        response = client.post(
            "/api/project-skills",
            json={"content": manifest("s")},
            headers={
                "X-CSRF-Token": "test-present",
                "X-User-Role": "project_admin",
                "X-Project-ID": "project-2",
            },
        )
    assert response.status_code == 403
    assert memory_s3.objects == {}


def test_unauthenticated_valid_write_leaves_no_objects(sessions, storage, memory_s3):
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    with TestClient(app) as client:
        response = client.post("/api/project-skills", json={"content": manifest("s")})
    assert response.status_code == 401
    assert memory_s3.objects == {}


def cookie_client(sessions, storage, token: str) -> TestClient:
    app = make_test_app(sessions, lambda: storage, context=None)
    assert app.state.allow_dev_identity is False
    client = TestClient(app)
    client.cookies.set("iap_session", token)
    return client


def test_missing_cookie_rejects_forged_development_identity(sessions, storage):
    """Catches dev headers authenticating when the isolated app disables dev identity."""
    client = TestClient(make_test_app(sessions, lambda: storage, context=None))
    response = client.get(
        "/api/project-skills",
        headers={
            "X-User-ID": "user-1",
            "X-Unit-ID": "unit-1",
            "X-Project-ID": "project-1",
            "X-User-Role": "project_admin",
        },
    )
    assert response.status_code == 401


def test_real_cookie_identity_reads_its_selected_project(sessions, storage):
    """Catches accidental dependence on a test-only request-context override."""
    skill_id = seed_skill(sessions, storage, make_context("skill.read"))
    token = issue_session(sessions)
    response = cookie_client(sessions, storage, token).get("/api/project-skills")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [skill_id]


def test_cookie_request_rejects_expired_and_revoked_sessions(sessions, storage):
    """Catches stale Cookie sessions remaining authorized."""
    for field in ("idle_expires_at", "revoked_at"):
        token = issue_session(sessions)
        with sessions() as session:
            auth = session.scalar(
                select(AuthSession).where(
                    AuthSession.session_token_hash
                    == hashlib.sha256(token.encode()).hexdigest()
                )
            )
            setattr(
                auth,
                field,
                (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                    if field == "idle_expires_at"
                    else datetime.now(timezone.utc)
                ),
            )
            session.commit()
        assert (
            cookie_client(sessions, storage, token)
            .get("/api/project-skills")
            .status_code
            == 401
        )


@pytest.mark.parametrize("mutation", ["membership", "permission"])
def test_cookie_request_rejects_member_or_role_permission_revocation(
    sessions, storage, mutation
):
    """Catches authorization snapshots that survive membership or grant revocation."""
    role_code = f"reader-{mutation}"
    with sessions() as session:
        seed_builtin_catalogue(session, "unit-1")
        role = Role(
            id=str(uuid4()),
            unit_id="unit-1",
            code=role_code,
            name=role_code,
            scope_type="project",
            built_in=False,
            status="active",
        )
        session.add(role)
        session.flush()
        session.add(
            RolePermission(
                id=str(uuid4()),
                role_id=role.id,
                permission_code="skill.read",
                unit_id="unit-1",
                data_scope="project",
            )
        )
        session.commit()
    token = issue_session(sessions, role_code=role_code)
    client = cookie_client(sessions, storage, token)
    initial = client.get("/api/project-skills")
    assert initial.status_code == 200, initial.text
    with sessions() as session:
        if mutation == "membership":
            membership = session.scalar(
                select(ProjectMembership).where(
                    ProjectMembership.user_id == "user-1",
                    ProjectMembership.project_id == "project-1",
                )
            )
            membership.status = "inactive"
        else:
            grant = session.scalar(
                select(RolePermission).join(Role).where(Role.code == role_code)
            )
            session.delete(grant)
        session.commit()
    response = client.get("/api/project-skills")
    assert response.status_code == 403


def test_forged_identity_headers_do_not_change_cookie_identity(sessions, storage):
    """Catches development headers overriding an authenticated Cookie identity."""
    own = make_context("skill.read")
    expected = seed_skill(sessions, storage, own, name="selected")
    seed_skill(
        sessions,
        storage,
        make_context(
            "skill.read", user_id="user-3", unit_id="unit-2", project_id="project-3"
        ),
        name="forged",
    )
    token = issue_session(sessions)
    response = cookie_client(sessions, storage, token).get(
        "/api/project-skills",
        headers={
            "X-User-ID": "user-3",
            "X-Unit-ID": "unit-2",
            "X-Project-ID": "project-3",
        },
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [expected]


def test_invalid_selected_project_is_not_replaced_by_sole_other_membership(
    sessions, storage
):
    """Catches identity fallback leaking a different project through this API."""
    alternative = make_context("skill.read", project_id="project-2")
    leaked_id = seed_skill(sessions, storage, alternative, name="must-not-leak")
    token = issue_session(sessions, project_id="project-1")
    with sessions() as session:
        role = session.scalar(
            select(Role).where(Role.code == "project_admin", Role.unit_id == "unit-1")
        )
        session.add_all(
            [
                ProjectMembership(
                    id=str(uuid4()),
                    user_id="user-1",
                    unit_id="unit-1",
                    project_id="project-2",
                    status="active",
                ),
                ProjectMembershipRole(
                    id=str(uuid4()),
                    user_id="user-1",
                    unit_id="unit-1",
                    project_id="project-2",
                    role_id=role.id,
                    scope_type="project",
                ),
            ]
        )
        original = session.scalar(
            select(ProjectMembership).where(
                ProjectMembership.user_id == "user-1",
                ProjectMembership.project_id == "project-1",
            )
        )
        original.status = "inactive"
        session.commit()

    response = cookie_client(sessions, storage, token).get("/api/project-skills")

    assert response.status_code == 403
    assert response.json() == {"detail": "skill_project_required"}
    assert leaked_id not in response.text


def test_invalid_selected_project_precedes_missing_fallback_permission(
    sessions, storage
):
    """Catches fallback capability denial masking an invalid selected project."""
    token = issue_session(sessions, project_id="project-1")
    with sessions() as session:
        no_read_role = Role(
            id=str(uuid4()),
            unit_id="unit-1",
            code="no-skill-read",
            name="No Skill Read",
            scope_type="project",
            built_in=False,
            status="active",
        )
        session.add_all(
            [
                no_read_role,
                ProjectMembership(
                    id=str(uuid4()),
                    user_id="user-1",
                    unit_id="unit-1",
                    project_id="project-2",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add(
            ProjectMembershipRole(
                id=str(uuid4()),
                user_id="user-1",
                unit_id="unit-1",
                project_id="project-2",
                role_id=no_read_role.id,
                scope_type="project",
            )
        )
        original = session.scalar(
            select(ProjectMembership).where(
                ProjectMembership.user_id == "user-1",
                ProjectMembership.project_id == "project-1",
            )
        )
        original.status = "inactive"
        session.commit()

    response = cookie_client(sessions, storage, token).get("/api/project-skills")

    assert response.status_code == 403
    assert response.json() == {"detail": "skill_project_required"}


def test_cookie_does_not_select_project_when_session_has_none(sessions, storage):
    """Catches implicit project selection for a Cookie with no selected project."""
    token = issue_session(sessions)
    with sessions() as session:
        auth = session.scalar(select(AuthSession))
        auth.current_project_id = None
        session.commit()
    response = cookie_client(sessions, storage, token).get("/api/project-skills")
    assert response.status_code == 403
    assert response.json() == {"detail": "skill_project_required"}


def test_authorization_version_change_invalidates_cookie(sessions, storage):
    """Catches stale authorization after user permission version changes."""
    token = issue_session(sessions)
    with sessions() as session:
        user = session.get(User, "user-1")
        user.authorization_version += 1
        session.commit()
    assert (
        cookie_client(sessions, storage, token).get("/api/project-skills").status_code
        == 401
    )


@pytest.mark.parametrize(
    "state,status", [("valid", 200), ("missing", 401), ("no-project", 403)]
)
def test_cookie_import_requires_authenticated_selected_project(
    sessions, storage, state, status
):
    from app.skills.models import Skill

    from backend.tests.skills.project_support import bundle, manifest

    token = issue_session(sessions)
    if state == "no-project":
        with sessions.begin() as session:
            session.scalar(select(AuthSession)).current_project_id = None
    client = cookie_client(sessions, storage, token)
    if state == "missing":
        client.cookies.clear()
    with client:
        response = client.post(
            "/api/project-skills/import",
            files={
                "file": (
                    "bundle.zip",
                    bundle([("SKILL.md", manifest("imported").encode())]),
                ),
            },
        )
    assert response.status_code == status
    with sessions() as session:
        skill = session.scalar(select(Skill))
        if state == "valid":
            assert (skill.project_id, skill.created_by) == ("project-1", "user-1")
        else:
            assert skill is None


OPERATIONS = (
    "list",
    "get",
    "draft",
    "versions",
    "version",
    "create",
    "save",
    "import",
    "publish",
)


def project_operation(client, operation, skill_id, version_id, *, headers=None):
    import json

    from backend.tests.skills.project_support import bundle

    root = "/api/project-skills"
    paths = {
        "list": root,
        "get": f"{root}/{skill_id}",
        "draft": f"{root}/{skill_id}/draft",
        "versions": f"{root}/{skill_id}/versions",
        "version": f"{root}/{skill_id}/versions/{version_id}",
    }
    headers = {"X-CSRF-Token": "test-present", **(headers or {})}
    if operation in paths:
        return client.get(paths[operation], headers=headers)
    if operation == "create":
        return client.post(root, json={"content": manifest("created")}, headers=headers)
    if operation == "save":
        return client.put(
            f"{root}/{skill_id}/draft",
            json={"expected_revision": 2, "content": manifest("s", "Saved")},
            headers=headers,
        )
    if operation == "import":
        return client.post(
            f"{root}/import",
            headers=headers,
            files={
                "file": (
                    "bundle.zip",
                    bundle([("SKILL.md", manifest("s", "Imported").encode())]),
                )
            },
            data={
                "manifest": json.dumps(
                    [
                        {
                            "source_name": "s",
                            "action": "update",
                            "skill_id": skill_id,
                            "expected_revision": 2,
                        }
                    ]
                )
            },
        )
    return client.post(
        f"{root}/{skill_id}/publish",
        json={"expected_revision": 2},
        headers={**headers, "Idempotency-Key": "cookie-publish"},
    )


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("state", ["valid", "missing", "no-permission", "cross-scope"])
def test_nine_method_cookie_authorization_matrix(sessions, storage, operation, state):
    from app.audit.models import AuditEvent
    from app.skills.models import Skill, SkillDraft, SkillVersion

    from backend.tests.skills.project_support import publish_skill

    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    version_id = publish_skill(sessions, make_context("skill.manage"), skill_id)
    if state == "no-permission":
        with sessions.begin() as session:
            session.add(
                Role(
                    id=str(uuid4()),
                    unit_id="unit-1",
                    code="no-skills",
                    name="No skills",
                    scope_type="project",
                    built_in=False,
                    status="active",
                )
            )
    token = issue_session(
        sessions,
        project_id="project-2" if state == "cross-scope" else "project-1",
        role_code="no-skills" if state == "no-permission" else "project_admin",
    )
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    with TestClient(app) as client:
        if state != "missing":
            client.cookies.set("iap_session", token)
        response = project_operation(client, operation, skill_id, version_id)
    expected = 201 if operation == "create" else 200
    if state == "missing":
        expected = 401
    elif state == "no-permission":
        expected = 403
    elif state == "cross-scope" and operation not in {"list", "create"}:
        expected = 404
    assert response.status_code == expected, response.text
    if expected < 300:
        assert response.headers["cache-control"] == "no-store"
    if state == "cross-scope" and operation == "list":
        assert response.json()["items"] == [] and response.json()["total"] == 0
    with sessions() as session:
        original = session.get(SkillDraft, skill_id)
        if state != "valid" or operation not in {"save", "import", "publish"}:
            assert original.revision == 2 and original.content == manifest("s")
            assert session.get(Skill, skill_id).published_version_id == version_id
            assert len(session.scalars(select(SkillVersion)).all()) == 1
        if state == "cross-scope" and operation == "create":
            created = session.get(Skill, response.json()["id"])
            assert (created.project_id, created.created_by) == ("project-2", "user-1")
        if expected >= 400:
            assert session.scalar(select(AuditEvent)) is None


@pytest.mark.parametrize("operation", ["save", "import", "publish"])
@pytest.mark.parametrize("failure", ["csrf", "origin"])
def test_cookie_write_matrix_rejects_missing_csrf_and_foreign_origin(
    sessions, storage, monkeypatch, operation, failure
):
    from app import main
    from app.audit.models import AuditEvent
    from app.skills.models import Skill, SkillDraft

    from backend.tests.skills.project_support import publish_skill

    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    version_id = publish_skill(sessions, make_context("skill.manage"), skill_id)
    token = issue_session(sessions)
    app = make_test_app(sessions, lambda: storage, with_write_protection=True)
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, public_base_url="https://platform.example"),
    )
    with TestClient(app) as client:
        client.cookies.set("iap_session", token)
        headers = (
            {"X-CSRF-Token": ""}
            if failure == "csrf"
            else {"Origin": "https://other.example"}
        )
        response = project_operation(
            client, operation, skill_id, version_id, headers=headers
        )
    assert response.status_code == 403
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 2
        assert session.get(Skill, skill_id).published_version_id == version_id
        assert session.scalar(select(AuditEvent)) is None


def test_cookie_publish_replay_rejects_revoked_manage_permission(sessions, storage):
    from app.audit.models import AuditEvent
    from app.skills.models import SkillDraft, SkillVersion

    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    token = issue_session(sessions)
    with TestClient(
        make_test_app(sessions, lambda: storage, with_write_protection=True)
    ) as client:
        client.cookies.set("iap_session", token)
        path = f"/api/project-skills/{skill_id}/publish"
        headers = {"X-CSRF-Token": "test-present", "Idempotency-Key": "same"}
        first = client.post(path, headers=headers, json={"expected_revision": 1})
        assert first.status_code == 200, first.text
        with sessions.begin() as session:
            grant = session.scalar(
                select(RolePermission)
                .join(Role)
                .where(
                    Role.code == "project_admin",
                    RolePermission.permission_code == "skill.manage",
                )
            )
            session.delete(grant)
        replay = client.post(path, headers=headers, json={"expected_revision": 1})
    assert replay.status_code == 403
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 2
        assert len(session.scalars(select(SkillVersion)).all()) == 1
        assert len(session.scalars(select(AuditEvent)).all()) == 1
