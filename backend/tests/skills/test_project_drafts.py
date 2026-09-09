from dataclasses import replace

import pytest
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.identity.models import Project
from app.skills.models import Skill, SkillDraft, SkillVersion
from app.skills.package_storage import SkillPackageStorageError
from app.skills.project_errors import ProjectSkillError
from app.skills.project_service import ProjectSkillService
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.tests.skills.project_support import (
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


def test_new_service_never_creates_legacy_directory_files(
    sessions, storage, monkeypatch, tmp_path
):
    from app.skills.service import SkillService

    legacy_root = tmp_path / "legacy-skills"
    original_init = SkillService.__init__

    def legacy_init(self, root=None):
        original_init(self, legacy_root)
        pytest.fail("Project skills constructed the legacy SkillService")

    monkeypatch.setattr(SkillService, "__init__", legacy_init)
    with client_for(sessions, storage) as client:
        created = client.post("/api/project-skills", json={"content": manifest("s")})
        assert created.status_code == 201
        changed = client.put(
            f"/api/project-skills/{created.json()['id']}/draft",
            json={"expected_revision": 1, "content": manifest("s", "Changed")},
        )
        assert changed.status_code == 200
    assert not legacy_root.exists()


def client_for(sessions, storage, context=None):
    return TestClient(
        make_test_app(
            sessions,
            lambda: storage,
            context=context or make_context("skill.read", "skill.manage"),
        )
    )


def test_create_only_draft_with_atomic_audit(sessions, storage):
    with client_for(sessions, storage) as client:
        response = client.post("/api/project-skills", json={"content": manifest("s")})
    assert response.status_code == 201
    value = response.json()
    assert value["draft_revision"] == 1
    assert value["published_version_id"] is None
    assert "object_key" not in value
    with sessions() as session:
        skill = session.get(Skill, value["id"])
        assert (skill.unit_id, skill.project_id, skill.created_by) == (
            "unit-1",
            "project-1",
            "user-1",
        )
        assert session.scalar(select(SkillVersion)) is None
        event = session.scalar(select(AuditEvent))
        assert (event.action, event.resource_id, event.status) == (
            "skill.create",
            skill.id,
            "succeeded",
        )
        assert (
            event.category,
            event.source,
            event.event_scope,
            event.authorization_scope,
            event.risk_level,
        ) == ("management", "system", "project", "project", "medium")
        assert event.metadata_json["revision"] == 1
        assert set(event.metadata_json) == {"revision", "digest"}


@pytest.mark.parametrize(
    "extra", ["unit_id", "project_id", "created_by", "object_key", "package_digest"]
)
def test_create_rejects_client_ownership(sessions, storage, extra):
    with client_for(sessions, storage) as client:
        response = client.post(
            "/api/project-skills", json={"content": manifest("s"), extra: "forged"}
        )
    assert response.status_code == 422
    with sessions() as session:
        assert session.scalar(select(Skill)) is None


def test_duplicate_name_returns_conflict(sessions, storage):
    seed_skill(sessions, storage, make_context("skill.manage"))
    with client_for(sessions, storage) as client:
        assert (
            client.post(
                "/api/project-skills", json={"content": manifest("s")}
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "body", ["", "x" * 200001, "not a manifest"], ids=["empty", "too-long", "invalid"]
)
def test_invalid_content_is_unprocessable(sessions, storage, body):
    with client_for(sessions, storage) as client:
        assert (
            client.post("/api/project-skills", json={"content": body}).status_code
            == 422
        )


@pytest.mark.parametrize("revision", [0, -1, True, "1", 1.0])
def test_revision_must_be_strict_positive_integer(sessions, storage, revision):
    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    with client_for(sessions, storage) as client:
        assert (
            client.put(
                f"/api/project-skills/{skill_id}/draft",
                json={"expected_revision": revision, "content": manifest("s")},
            ).status_code
            == 422
        )


def test_no_permission_never_constructs_storage(sessions):
    def forbidden_storage():
        pytest.fail("unauthorized request constructed storage")

    app = make_test_app(sessions, forbidden_storage, context=make_context("skill.read"))
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/project-skills", json={"content": manifest("s")}
            ).status_code
            == 403
        )


def requests():
    from app.skills.project_schemas import ProjectSkillCreate, ProjectSkillDraftUpdate

    return ProjectSkillCreate, ProjectSkillDraftUpdate


def test_edit_preserves_reference_bytes_and_old_object(sessions, storage):
    from app.skills.project_packages import read_verified_package, snapshot_draft

    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(
        sessions,
        storage,
        context,
        attachments=(("references/rules.txt", b"check inflow"),),
    )
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    _, update = requests()
    result = ProjectSkillService(sessions, storage_factory=lambda: storage).save_draft(
        context,
        skill_id,
        update(expected_revision=1, content=manifest("s", "Changed instructions")),
    )
    with sessions() as session:
        current = snapshot_draft(session.get(SkillDraft, skill_id))
    assert result.revision == 2
    assert {
        item.path: item.data for item in read_verified_package(storage, current).files
    }["references/rules.txt"] == b"check inflow"
    assert read_verified_package(storage, original).content == manifest("s")


def test_stale_save_keeps_committed_draft(sessions, storage):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    _, update = requests()
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    saved = service.save_draft(
        context,
        skill_id,
        update(expected_revision=1, content=manifest("s", "First edit")),
    )
    with pytest.raises(ProjectSkillError) as caught:
        service.save_draft(
            context,
            skill_id,
            update(expected_revision=1, content=manifest("s", "Stale edit")),
        )
    assert caught.value.code == "skill_revision_conflict"
    assert service.get_draft(context, skill_id).content == saved.content


class FailingAudit(AuditRecorder):
    def record(self, session, request):
        super().record(session, request)
        raise RuntimeError("audit failed after insertion")


@pytest.mark.parametrize("operation", ["create", "save"])
def test_audit_failure_rolls_back_real_rows(sessions, storage, operation):
    context = make_context("skill.read", "skill.manage")
    create, update = requests()
    skill_id = seed_skill(sessions, storage, context) if operation == "save" else None
    service = ProjectSkillService(
        sessions, storage_factory=lambda: storage, audit_recorder=FailingAudit()
    )
    with pytest.raises(RuntimeError, match="audit failed"):
        if skill_id:
            service.save_draft(
                context,
                skill_id,
                update(expected_revision=1, content=manifest("s", "Changed")),
            )
        else:
            service.create(context, create(content=manifest("s")))
    with sessions() as session:
        assert session.scalar(select(AuditEvent)) is None
        if skill_id:
            draft = session.get(SkillDraft, skill_id)
            assert (draft.revision, draft.content) == (1, manifest("s"))
        else:
            assert session.scalar(select(Skill)) is None
            assert session.scalar(select(SkillDraft)) is None


def test_json_rename_rejected_and_large_existing_body_returned(sessions, storage):
    skill_id = seed_skill(
        sessions, storage, make_context("skill.manage"), body="x" * 210000
    )
    with client_for(sessions, storage) as client:
        assert (
            len(client.get(f"/api/project-skills/{skill_id}/draft").json()["content"])
            > 200000
        )
        assert (
            client.put(
                f"/api/project-skills/{skill_id}/draft",
                json={"expected_revision": 1, "content": manifest("renamed")},
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "damage", ["archive_sha256", "files", "content", "object_scope"]
)
def test_corrupt_draft_never_saved(sessions, storage, memory_s3, damage):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    with sessions() as session:
        draft = session.get(SkillDraft, skill_id)
        if damage == "archive_sha256":
            draft.archive_sha256 = "0" * 64
        elif damage == "files":
            draft.files = [{"path": "SKILL.md", "size": 0, "sha256": "0" * 64}]
        elif damage == "content":
            draft.content = "corrupt"
        else:
            draft.object_key = draft.object_key.replace("project-1", "project-2")
        session.commit()
    _, update = requests()
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).save_draft(
            context, skill_id, update(expected_revision=1, content=manifest("s"))
        )
    assert (caught.value.code, caught.value.status_code) == (
        "skill_storage_unavailable",
        503,
    )
    if damage == "object_scope":
        assert memory_s3.get_calls == 0
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.scalar(select(AuditEvent)) is None


def test_storage_failure_keeps_draft(sessions, storage, monkeypatch):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)

    def fail(*args):
        raise SkillPackageStorageError("unavailable")

    monkeypatch.setattr(storage, "put", fail)
    _, update = requests()
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).save_draft(
            context,
            skill_id,
            update(expected_revision=1, content=manifest("s", "Changed")),
        )
    assert caught.value.status_code == 503
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).content == manifest("s")


@pytest.mark.parametrize("mutation", ["content", "project"])
def test_reauthorizes_and_compares_full_snapshot_after_upload(
    sessions, storage, monkeypatch, mutation
):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    put = storage.put

    def race(*args):
        stored = put(*args)
        with sessions() as session:
            if mutation == "project":
                session.get(Project, context.project_id).status = "inactive"
            else:
                session.get(SkillDraft, skill_id).content = "concurrent corruption"
            session.commit()
        return stored

    monkeypatch.setattr(storage, "put", race)
    _, update = requests()
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).save_draft(
            context,
            skill_id,
            update(expected_revision=1, content=manifest("s", "Changed")),
        )
    assert caught.value.status_code == (403 if mutation == "project" else 409)
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.scalar(select(AuditEvent)) is None


def test_own_create_requires_actor_in_owner_scope(sessions, storage):
    context = make_context("skill.manage", data_scope="own")
    authorization = context.authorization_context
    grant = replace(authorization.grants[0], owner_user_id="user-2")
    context.authorization_context = authorization.model_copy(
        update={"grants": (grant,)}
    )
    create, _ = requests()
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).create(
            context, create(content=manifest("s"))
        )
    assert caught.value.status_code == 403
