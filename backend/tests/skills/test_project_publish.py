import pytest
from app.audit.models import AuditEvent
from app.skills.models import Skill, SkillDraft, SkillVersion
from app.skills.project_errors import ProjectSkillError
from app.skills.project_schemas import ProjectSkillDraftUpdate, ProjectSkillPublish
from app.skills.project_service import ProjectSkillService
from app.skills.repository import SkillRepository, SkillResourceNotFound, SkillScope
from sqlalchemy import select

from backend.tests.skills.project_support import make_context, manifest, seed_skill
from backend.tests.skills.project_support import (
    memory_s3_fixture as _memory_s3_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    sessions_fixture as _sessions_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    storage_fixture as _storage_fixture,  # noqa: F401
)


def test_publish_commits_verified_snapshot_revision_pointer_and_one_audit(
    sessions, storage
):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    result = ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
        context, skill_id, ProjectSkillPublish(expected_revision=1), "publish-1"
    )
    assert (result.version, result.source_revision, result.published_by) == (
        1,
        1,
        "user-1",
    )
    assert "content" not in result.model_dump()
    assert "object_key" not in result.model_dump()
    with sessions() as session:
        version = session.get(SkillVersion, result.id)
        assert version.content == manifest("s")
        assert session.get(SkillDraft, skill_id).revision == 2
        assert session.get(Skill, skill_id).published_version_id == result.id
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.resource_id == skill_id)
        )
        assert event.action == "skill.publish"
        assert event.metadata_json == {
            "revision": 1,
            "digest": version.package_digest,
            "version_id": result.id,
        }


@pytest.mark.parametrize("later_draft", [False, True])
def test_publish_replay_survives_later_draft_and_storage_failure(
    sessions, storage, later_draft
):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    request = ProjectSkillPublish(expected_revision=1)
    first = service.publish(context, skill_id, request, "publish-1")
    if later_draft:
        service.save_draft(
            context,
            skill_id,
            ProjectSkillDraftUpdate(
                expected_revision=2, content=manifest("s", "Later draft")
            ),
        )

    def unavailable():
        raise AssertionError("A committed replay must not initialize storage")

    replay = ProjectSkillService(sessions, storage_factory=unavailable).publish(
        context, skill_id, request, "publish-1"
    )
    assert replay.model_dump() == first.model_dump()
    with sessions() as session:
        assert (
            len(
                session.scalars(
                    select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.resource_id == skill_id,
                        AuditEvent.action == "skill.publish",
                    )
                ).all()
            )
            == 1
        )
        assert session.get(SkillDraft, skill_id).revision == (3 if later_draft else 2)


@pytest.mark.parametrize(
    "case,code,status",
    [
        ("revision", "skill_idempotency_conflict", 409),
        ("user", "skill_idempotency_conflict", 409),
        ("permission", "skill_permission_denied", 403),
        ("scope", "skill_not_found", 404),
        ("owner", "skill_not_found", 404),
    ],
)
def test_replay_requires_same_request_and_current_authorization(
    sessions, storage, case, code, status
):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    request = ProjectSkillPublish(expected_revision=1)
    ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
        context, skill_id, request, "key"
    )
    if case == "revision":
        request = ProjectSkillPublish(expected_revision=2)
    elif case == "user":
        context = make_context("skill.manage", user_id="user-2")
    elif case == "permission":
        context = make_context("skill.read")
    elif case == "scope":
        context = make_context("skill.manage", project_id="project-2")
    elif case == "owner":
        context = make_context("skill.manage", user_id="user-2", data_scope="own")

    def unavailable():
        pytest.fail("Replay must resolve without storage")

    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=unavailable).publish(
            context, skill_id, request, "key"
        )
    assert (caught.value.code, caught.value.status_code) == (code, status)


@pytest.mark.parametrize("key", ["", "x" * 129, "\u00e9", "line\n", "\x7f", None, 4])
def test_direct_publish_rejects_invalid_keys_before_mutation(sessions, storage, key):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
            context, skill_id, ProjectSkillPublish(expected_revision=1), key
        )
    assert caught.value.status_code == 422
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.scalar(select(SkillVersion)) is None
        assert session.scalar(select(AuditEvent)) is None


@pytest.mark.parametrize("revision", [True, 1.0, 0, -1, "1"])
def test_direct_publish_revalidates_constructed_revision(sessions, storage, revision):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
            context,
            skill_id,
            ProjectSkillPublish.model_construct(expected_revision=revision),
            "key",
        )
    assert caught.value.status_code == 422
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.scalar(select(SkillVersion)) is None


def test_repository_replay_preserves_historical_digest_and_scopes_before_key(
    sessions, storage
):
    import hashlib

    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    scope = SkillScope(context.unit_id, context.project_id)
    with sessions.begin() as session:
        row = SkillRepository(session).publish(
            scope,
            skill_id,
            expected_revision=1,
            idempotency_key=" key ",
            published_by="user-1",
        )
        version_id = row.id
        expected = (
            '{"expected_revision":1,"operation":"publish","published_by":"user-1","skill_id":"'
            + skill_id
            + '"}'
        )
        assert row.request_digest == hashlib.sha256(expected.encode()).hexdigest()
    with sessions() as session:
        repository = SkillRepository(session)
        replay = repository.find_publish_replay(
            scope,
            skill_id,
            expected_revision=1,
            idempotency_key=" key ",
            published_by="user-1",
        )
        assert replay.id == version_id
        for other_scope, owners in [
            (SkillScope("unit-1", "project-2"), None),
            (scope, frozenset()),
        ]:
            with pytest.raises(SkillResourceNotFound):
                repository.find_publish_replay(
                    other_scope,
                    skill_id,
                    expected_revision=2,
                    idempotency_key=" key ",
                    published_by="user-2",
                    owner_ids=owners,
                )


def test_same_named_skills_in_different_projects_do_not_share_replay(sessions, storage):
    versions = []
    for project in ("project-1", "project-2"):
        context = make_context("skill.manage", project_id=project)
        skill_id = seed_skill(sessions, storage, context)
        versions.append(
            ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
                context,
                skill_id,
                ProjectSkillPublish(expected_revision=1),
                "shared-key",
            )
        )
    assert versions[0].id != versions[1].id
    assert versions[0].skill_id != versions[1].skill_id


@pytest.mark.parametrize(
    "damage", ["content", "files", "archive_sha256", "object_scope"]
)
def test_corrupt_publish_preserves_previous_version_pointer(
    sessions, storage, memory_s3, damage
):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    first = service.publish(
        context, skill_id, ProjectSkillPublish(expected_revision=1), "first"
    )
    with sessions.begin() as session:
        draft = session.get(SkillDraft, skill_id)
        if damage == "content":
            draft.content = "corrupt"
        elif damage == "files":
            draft.files = []
        elif damage == "archive_sha256":
            draft.archive_sha256 = "0" * 64
        else:
            draft.object_key = draft.object_key.replace("project-1", "project-2")
    reads = memory_s3.get_calls
    with pytest.raises(ProjectSkillError) as caught:
        service.publish(
            context, skill_id, ProjectSkillPublish(expected_revision=2), "second"
        )
    assert (caught.value.code, caught.value.status_code) == (
        "skill_storage_unavailable",
        503,
    )
    if damage == "object_scope":
        assert memory_s3.get_calls == reads
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 2
        assert session.get(Skill, skill_id).published_version_id == first.id
        assert len(session.scalars(select(SkillVersion)).all()) == 1
        assert len(session.scalars(select(AuditEvent)).all()) == 1


@pytest.mark.parametrize(
    "outcome", ["read-success", "known-failure", "unknown-failure"]
)
def test_concurrent_commit_during_storage_read_replays_only_known_failures(
    sessions, storage, monkeypatch, outcome
):
    from app.skills.package_storage import SkillPackageStorage, SkillPackageStorageError

    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    request = ProjectSkillPublish(expected_revision=1)
    competitor_storage = SkillPackageStorage(storage._client, storage._bucket)
    competitor = ProjectSkillService(
        sessions, storage_factory=lambda: competitor_storage
    )
    read = storage.read
    committed = []

    def race(*args):
        committed.append(competitor.publish(context, skill_id, request, "same"))
        if outcome == "known-failure":
            raise SkillPackageStorageError("unavailable after competitor committed")
        if outcome == "unknown-failure":
            raise RuntimeError("unexpected read bug")
        return read(*args)

    monkeypatch.setattr(storage, "read", race)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    if outcome == "unknown-failure":
        with pytest.raises(RuntimeError, match="unexpected read bug"):
            service.publish(context, skill_id, request, "same")
    else:
        assert service.publish(context, skill_id, request, "same") == committed[0]
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 2
        assert len(session.scalars(select(SkillVersion)).all()) == 1
        assert len(session.scalars(select(AuditEvent)).all()) == 1


@pytest.mark.parametrize(
    "field",
    [
        "content",
        "description",
        "display_version",
        "files",
        "object_key",
        "archive_sha256",
        "package_digest",
        "size_bytes",
        "project",
    ],
)
def test_publish_reauthorizes_and_compares_full_snapshot_after_verification(
    sessions, storage, monkeypatch, field
):
    from app.identity.models import Project

    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    read = storage.read

    def change_after_read(*args):
        data = read(*args)
        with sessions.begin() as session:
            if field == "project":
                session.get(Project, context.project_id).status = "inactive"
            else:
                draft = session.get(SkillDraft, skill_id)
                values = {
                    "content": "changed",
                    "description": "changed",
                    "display_version": "2.0",
                    "files": [],
                    "object_key": draft.object_key.replace("project-1", "project-2"),
                    "archive_sha256": "0" * 64,
                    "package_digest": "0" * 64,
                    "size_bytes": 1,
                }
                setattr(draft, field, values[field])
        return data

    monkeypatch.setattr(storage, "read", change_after_read)
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).publish(
            context, skill_id, ProjectSkillPublish(expected_revision=1), "key"
        )
    assert caught.value.status_code == (403 if field == "project" else 409)
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.get(Skill, skill_id).published_version_id is None
        assert session.scalar(select(SkillVersion)) is None
        assert session.scalar(select(AuditEvent)) is None
