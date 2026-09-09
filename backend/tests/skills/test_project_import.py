import json

import pytest
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.skills.models import Skill, SkillDraft
from app.skills.package import parse_skill_bundle
from app.skills.package_storage import SkillPackageStorage
from app.skills.project_errors import ProjectSkillError
from app.skills.project_packages import read_verified_package, snapshot_draft
from app.skills.project_service import ProjectSkillService
from sqlalchemy import event, func, select

from backend.tests.skills.project_support import (
    bundle,
    make_context,
    manifest,
    publish_skill,
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


def two_packages():
    return bundle(
        [(f"{name}/SKILL.md", manifest(name).encode()) for name in ("a", "b")]
    )


def test_default_import_creates_all_drafts(sessions, storage):
    result = ProjectSkillService(
        sessions, storage_factory=lambda: storage
    ).import_bundle(make_context("skill.manage"), two_packages(), None)
    assert result.created_count == 2
    assert [(item.name, item.draft_revision) for item in result.items] == [
        ("a", 1),
        ("b", 1),
    ]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Skill)) == 2
        events = session.scalars(select(AuditEvent)).all()
        assert len(events) == 2
        assert {item.action for item in events} == {"skill.import"}
        assert len({item.trace_id for item in events}) == 1


def test_import_does_not_partially_commit_when_second_object_fails(sessions, memory_s3):
    class FailSecondPut(type(memory_s3)):
        def put_object(self, **request):
            if len(self.objects) == 1:
                raise OSError("simulated store failure")
            return super().put_object(**request)

    storage = SkillPackageStorage(FailSecondPut(), "test-skills")
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    with pytest.raises(ProjectSkillError) as caught:
        service.import_bundle(make_context("skill.manage"), two_packages(), None)
    assert caught.value.status_code == 503
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Skill)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


class FailSecondAudit(AuditRecorder):
    def __init__(self):
        self.count = 0

    def record(self, session, request):
        result = super().record(session, request)
        self.count += 1
        if self.count == 2:
            raise RuntimeError("second audit failed")
        return result


def mixed_manifest(skill_id, revision):
    return json.dumps(
        [
            {"action": "create", "source_name": "a"},
            {
                "action": "update",
                "source_name": "b",
                "skill_id": skill_id,
                "expected_revision": revision,
            },
        ]
    )


@pytest.mark.parametrize("failure", ["stale", "audit", "commit"])
def test_mixed_import_failure_preserves_draft_and_publication(
    sessions, storage, failure
):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context, name="original")
    version_id = publish_skill(sessions, context, skill_id)
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    service = ProjectSkillService(
        sessions,
        storage_factory=lambda: storage,
        audit_recorder=FailSecondAudit() if failure == "audit" else None,
    )

    def fail_commit(session):
        raise RuntimeError("database commit failed")

    if failure == "commit":
        event.listen(sessions, "before_commit", fail_commit)
    try:
        with pytest.raises(
            ProjectSkillError if failure == "stale" else RuntimeError
        ) as caught:
            service.import_bundle(
                context,
                two_packages(),
                mixed_manifest(
                    skill_id,
                    original.revision - 1 if failure == "stale" else original.revision,
                ),
            )
        if failure == "stale":
            assert caught.value.code == "skill_revision_conflict"
    finally:
        if failure == "commit":
            event.remove(sessions, "before_commit", fail_commit)
    with sessions() as session:
        assert snapshot_draft(session.get(SkillDraft, skill_id)) == original
        assert session.get(Skill, skill_id).published_version_id == version_id
        assert session.scalar(select(func.count()).select_from(Skill)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_mixed_import_renames_only_frontmatter_and_preserves_published_pointer(
    sessions, storage
):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context, name="original")
    published_id = publish_skill(sessions, context, skill_id)
    data = bundle(
        [
            ("a/SKILL.md", manifest("a", "a stays in body").encode()),
            ("a/ref.txt", b"a stays in attachment"),
            ("b/SKILL.md", manifest("b", "b stays in body").encode()),
            ("c/SKILL.md", manifest("c").encode()),
        ]
    )
    entries = [
        {"action": "skip", "source_name": "c"},
        {
            "action": "update",
            "source_name": "b",
            "skill_id": skill_id,
            "expected_revision": 2,
        },
        {"action": "create", "source_name": "a", "target_name": "renamed"},
    ]
    result = ProjectSkillService(
        sessions, storage_factory=lambda: storage
    ).import_bundle(context, data, json.dumps(entries))
    assert (result.created_count, result.updated_count, result.skipped_count) == (
        1,
        1,
        1,
    )
    assert [item.source_name for item in result.items] == ["c", "b", "a"]
    assert result.items[0].model_dump() == {
        "source_name": "c",
        "action": "skip",
        "skill_id": None,
        "name": "c",
        "draft_revision": None,
    }
    assert result.items[1].draft_revision == 3
    with sessions() as session:
        updated = snapshot_draft(session.get(SkillDraft, skill_id))
        created = snapshot_draft(session.get(SkillDraft, result.items[2].skill_id))
        assert session.get(Skill, skill_id).published_version_id == published_id
    assert updated.name == "original"
    assert "b stays in body" in updated.content
    assert "a stays in body" in created.content
    assert created.stored.package_digest != parse_skill_bundle(data)[0].digest
    assert {
        item.path: item.data for item in read_verified_package(storage, created).files
    }["ref.txt"] == b"a stays in attachment"


@pytest.mark.parametrize(
    "entries",
    [
        [],
        {},
        [{"source_name": "a", "action": "unknown"}],
        [{"source_name": "a", "action": "skip"}],
        [
            {"source_name": "a", "action": "skip"},
            {"source_name": "a", "action": "skip"},
        ],
        [
            {"source_name": "a", "action": "create", "target_name": "Invalid"},
            {"source_name": "b", "action": "skip"},
        ],
        [
            {"source_name": "a", "action": "skip", "skill_id": "forged"},
            {"source_name": "b", "action": "skip"},
        ],
        [
            {"source_name": "a", "action": "create", "unit_id": "forged"},
            {"source_name": "b", "action": "skip"},
        ],
        [
            {
                "source_name": "a",
                "action": "update",
                "skill_id": "invalid",
                "expected_revision": 1,
            },
            {"source_name": "b", "action": "skip"},
        ],
        [{"source_name": "a", "action": "skip"}] * 501,
    ],
)
def test_manifest_validation_precedes_storage(sessions, entries):
    def forbidden_storage():
        pytest.fail("invalid manifest constructed storage")

    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=forbidden_storage).import_bundle(
            make_context("skill.manage"), two_packages(), json.dumps(entries)
        )
    assert (caught.value.code, caught.value.status_code) == (
        "skill_import_manifest_invalid",
        422,
    )


@pytest.mark.parametrize(
    "manifest_json",
    ["{", '"' + "\u6c34" * 90000 + '"'],
    ids=["invalid-json", "utf8-too-large"],
)
def test_invalid_or_utf8_oversized_manifest_is_422(sessions, storage, manifest_json):
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).import_bundle(
            make_context("skill.manage"), two_packages(), manifest_json
        )
    assert caught.value.code == "skill_import_manifest_invalid"


@pytest.mark.parametrize("revision", [0, -1, True, "1", 1.0])
def test_update_manifest_revision_is_strict(sessions, storage, revision):
    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).import_bundle(
            make_context("skill.manage"),
            two_packages(),
            mixed_manifest(skill_id, revision),
        )
    assert caught.value.code == "skill_import_manifest_invalid"


@pytest.mark.parametrize("conflict", ["existing", "batch-name", "repeated-update"])
def test_conflicting_targets_are_rejected_before_upload(
    sessions, storage, memory_s3, conflict
):
    context = make_context("skill.manage")
    skill_id = seed_skill(sessions, storage, context, name="a")
    count = len(memory_s3.objects)
    entries = None
    if conflict == "batch-name":
        entries = json.dumps(
            [
                {"action": "create", "source_name": "a", "target_name": "new"},
                {"action": "create", "source_name": "b", "target_name": "new"},
            ]
        )
    if conflict == "repeated-update":
        entries = json.dumps(
            [
                {
                    "action": "update",
                    "source_name": source,
                    "skill_id": skill_id,
                    "expected_revision": 1,
                }
                for source in ("a", "b")
            ]
        )
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).import_bundle(
            context, two_packages(), entries
        )
    assert caught.value.code == (
        "skill_import_manifest_invalid"
        if conflict == "repeated-update"
        else "skill_name_conflict"
    )
    assert len(memory_s3.objects) == count


def test_all_skip_never_constructs_storage_or_reads_resources(sessions, monkeypatch):
    from app.skills.repository import SkillRepository

    def forbidden(*args, **kwargs):
        pytest.fail("skip accessed resources or storage")

    monkeypatch.setattr(SkillRepository, "get_draft", forbidden)
    result = ProjectSkillService(sessions, storage_factory=forbidden).import_bundle(
        make_context("skill.manage"),
        two_packages(),
        json.dumps([{"source_name": name, "action": "skip"} for name in ("a", "b")]),
    )
    assert (result.created_count, result.updated_count, result.skipped_count) == (
        0,
        0,
        2,
    )
    with sessions() as session:
        assert session.scalar(select(AuditEvent)) is None


@pytest.mark.parametrize("visibility", ["own", "project"])
def test_update_authorization_precedes_all_uploads(
    sessions, storage, memory_s3, visibility
):
    skill_id = seed_skill(
        sessions, storage, make_context("skill.manage", user_id="user-2")
    )
    count = len(memory_s3.objects)
    context = (
        make_context("skill.manage", data_scope="own")
        if visibility == "own"
        else make_context("skill.manage", project_id="project-2")
    )
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions, storage_factory=lambda: storage).import_bundle(
            context, two_packages(), mixed_manifest(skill_id, 1)
        )
    assert caught.value.code == "skill_not_found"
    assert len(memory_s3.objects) == count
