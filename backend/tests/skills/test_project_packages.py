from dataclasses import replace

import pytest
from app.skills.models import SkillDraft
from app.skills.package import parse_skill_bundle
from app.skills.project_errors import ProjectSkillError

from backend.tests.skills.project_support import (
    bundle,
    make_context,
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


@pytest.mark.parametrize(
    "body",
    [
        b"not a zip",
        bundle([("../SKILL.md", b"bad")]),
        bundle(
            [
                ("s/SKILL.md", manifest("s").encode()),
                ("t/SKILL.md", manifest("t").encode()),
            ]
        ),
    ],
    ids=["invalid-zip", "unsafe-path", "multiple-packages"],
)
def test_valid_archive_reference_still_rejects_invalid_package(
    sessions, storage, memory_s3, body
):
    import hashlib

    from app.skills.project_packages import read_verified_package, snapshot_draft

    skill_id = seed_skill(sessions, storage, make_context("skill.read"))
    with sessions() as session:
        snapshot = snapshot_draft(session.get(SkillDraft, skill_id))
    stored = replace(
        snapshot.stored,
        archive_sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )
    record = memory_s3.objects[("project-skill-tests", stored.object_key)]
    record["Body"] = body
    record["Metadata"]["archive-sha256"] = stored.archive_sha256
    record["Metadata"]["size-bytes"] = str(len(body))
    with pytest.raises(ProjectSkillError) as caught:
        read_verified_package(storage, replace(snapshot, stored=stored))
    assert caught.value.status_code == 503


def test_verified_read_compares_normalized_digest_even_when_storage_metadata_matches(
    sessions, storage, memory_s3
):
    from app.skills.project_packages import read_verified_package, snapshot_draft

    skill_id = seed_skill(sessions, storage, make_context("skill.read"))
    with sessions() as session:
        snapshot = snapshot_draft(session.get(SkillDraft, skill_id))
    stored = replace(snapshot.stored, package_digest="0" * 64)
    memory_s3.objects[("project-skill-tests", stored.object_key)]["Metadata"][
        "package-digest"
    ] = stored.package_digest
    with pytest.raises(ProjectSkillError) as caught:
        read_verified_package(storage, replace(snapshot, stored=stored))
    assert caught.value.status_code == 503


def test_manifest_replacement_preserves_bytes_and_revalidates():
    from app.skills.project_packages import package_from_content, replace_manifest

    package = parse_skill_bundle(
        bundle(
            [("SKILL.md", manifest("s").encode()), ("references/x.bin", b"\x00\xff")]
        )
    )[0]
    changed = replace_manifest(package, manifest("s", "Edited"))
    assert changed.digest != package.digest
    assert {item.path: item.data for item in changed.files}[
        "references/x.bin"
    ] == b"\x00\xff"
    assert package_from_content(manifest("s")).content == manifest("s")


def test_snapshot_detaches_nested_manifest(sessions, storage):
    from app.skills.project_packages import snapshot_draft

    skill_id = seed_skill(sessions, storage, make_context("skill.read"))
    with sessions() as session:
        draft = session.get(SkillDraft, skill_id)
        snapshot = snapshot_draft(draft)
        draft.files[0]["path"] = "changed"
        assert snapshot.files[0][0] == "SKILL.md"
    assert snapshot.name == "s"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "other"),
        ("description", "other"),
        ("display_version", "2"),
        ("content", "other"),
        ("files", ()),
    ],
)
def test_verified_read_rejects_each_mismatched_snapshot_field(
    sessions, storage, field, value
):
    from app.skills.project_packages import read_verified_package, snapshot_draft

    skill_id = seed_skill(sessions, storage, make_context("skill.read"))
    with sessions() as session:
        snapshot = snapshot_draft(session.get(SkillDraft, skill_id))
    with pytest.raises(ProjectSkillError) as caught:
        read_verified_package(storage, replace(snapshot, **{field: value}))
    assert (caught.value.code, caught.value.status_code) == (
        "skill_storage_unavailable",
        503,
    )
