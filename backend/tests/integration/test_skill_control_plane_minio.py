import os
from uuid import uuid4

import boto3
import pytest
from app.audit.models import AuditEvent
from app.skills.models import Skill, SkillDraft, SkillVersion
from app.skills.package_storage import create_default_skill_package_storage
from app.skills.project_errors import ProjectSkillError
from app.skills.project_packages import read_verified_package, snapshot_draft
from app.skills.project_schemas import ProjectSkillDraftUpdate, ProjectSkillPublish
from app.skills.project_service import ProjectSkillService
from botocore.config import Config
from sqlalchemy import select

from backend.tests.integration.test_skill_control_plane_postgres import (
    pg_environment as _pg_environment,  # noqa: F401
)
from backend.tests.skills.project_support import manifest, seed_skill
from backend.tests.skills.test_project_drafts import FailingAudit
from backend.tests.skills.test_project_import import (
    FailSecondAudit,
    mixed_manifest,
    two_packages,
)


@pytest.fixture
def minio_storage(monkeypatch):
    names = ("TEST_S3_ENDPOINT", "TEST_S3_ACCESS_KEY", "TEST_S3_SECRET_KEY")
    configuration = {name: os.environ.get(name) for name in names}
    if not all(configuration.values()):
        pytest.skip("Dedicated MinIO test configuration is required")
    client = boto3.client(
        "s3",
        endpoint_url=configuration[names[0]],
        aws_access_key_id=configuration[names[1]],
        aws_secret_access_key=configuration[names[2]],
        region_name="us-east-1",
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
    )
    bucket = f"iap-control-test-{uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ENDPOINT", configuration[names[0]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ACCESS_KEY", configuration[names[1]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_SECRET_KEY", configuration[names[2]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_REGION", "us-east-1")
    monkeypatch.setenv("IAP_SKILL_BUCKET", bucket)
    try:
        yield create_default_skill_package_storage(), client, bucket
    finally:
        # This bucket was created by this fixture; no shared bucket is enumerated.
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)
        client.close()


def test_minio_verified_publish_replays_after_later_draft_and_original_object_loss(
    pg_environment, minio_storage
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(
        sessions,
        storage,
        context,
        attachments=(("references/control.txt", b"control limits\x00\xff"),),
    )
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    service = ProjectSkillService(sessions)
    request = ProjectSkillPublish(expected_revision=1)
    first = service.publish(context, skill_id, request, "minio-publish")
    assert {
        item.path: item.data for item in read_verified_package(storage, original).files
    }["references/control.txt"] == b"control limits\x00\xff"
    service.save_draft(
        context,
        skill_id,
        ProjectSkillDraftUpdate(
            expected_revision=2, content=manifest("s", "Later draft")
        ),
    )
    client.delete_object(Bucket=bucket, Key=original.stored.object_key)

    def unavailable():
        raise AssertionError("Committed replay must not create a MinIO client")

    replay = ProjectSkillService(sessions, storage_factory=unavailable).publish(
        context, skill_id, request, "minio-publish"
    )
    assert replay == first
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 3
        assert session.get(SkillDraft, skill_id).content == manifest("s", "Later draft")
        assert session.get(Skill, skill_id).published_version_id == first.id
        versions = session.scalars(
            select(SkillVersion).where(SkillVersion.skill_id == skill_id)
        ).all()
        assert len(versions) == 1 and versions[0].content == manifest("s")
        audits = session.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == skill_id, AuditEvent.action == "skill.publish"
            )
        ).all()
        assert len(audits) == 1


@pytest.mark.parametrize("damage", ["archive", "metadata", "missing", "snapshot"])
def test_minio_publish_rejects_corruption_without_changing_existing_publication(
    pg_environment, minio_storage, damage
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(sessions, storage, context)
    service = ProjectSkillService(sessions)
    first = service.publish(
        context, skill_id, ProjectSkillPublish(expected_revision=1), "first"
    )
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    if damage == "missing":
        client.delete_object(Bucket=bucket, Key=original.stored.object_key)
    elif damage == "snapshot":
        with sessions.begin() as session:
            session.get(SkillDraft, skill_id).files = []
    else:
        response = client.get_object(Bucket=bucket, Key=original.stored.object_key)
        data = response["Body"].read()
        response["Body"].close()
        metadata = response["Metadata"]
        if damage == "archive":
            data = data[:-1] + bytes([data[-1] ^ 1])
        else:
            metadata["package-digest"] = "0" * 64
        client.put_object(
            Bucket=bucket, Key=original.stored.object_key, Body=data, Metadata=metadata
        )
    with sessions() as session:
        before_publish = snapshot_draft(session.get(SkillDraft, skill_id))
    with pytest.raises(ProjectSkillError) as caught:
        service.publish(
            context, skill_id, ProjectSkillPublish(expected_revision=2), "second"
        )
    assert (caught.value.code, caught.value.status_code) == (
        "skill_storage_unavailable",
        503,
    )
    with sessions() as session:
        assert snapshot_draft(session.get(SkillDraft, skill_id)) == before_publish
        assert session.get(Skill, skill_id).published_version_id == first.id
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
                    select(AuditEvent).where(AuditEvent.resource_id == skill_id)
                ).all()
            )
            == 1
        )


def test_minio_default_factory_preserves_attachments_and_previous_archive(
    pg_environment, minio_storage
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(
        sessions,
        storage,
        context,
        attachments=(("references/rules.txt", b"check inflow\x00\xff"),),
    )
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    saved = ProjectSkillService(sessions).save_draft(
        context,
        skill_id,
        ProjectSkillDraftUpdate(expected_revision=1, content=manifest("s", "Changed")),
    )
    with sessions() as session:
        current = snapshot_draft(session.get(SkillDraft, skill_id))
    assert saved.revision == 2
    assert read_verified_package(storage, original).content == manifest("s")
    assert {
        item.path: item.data for item in read_verified_package(storage, current).files
    }["references/rules.txt"] == b"check inflow\x00\xff"
    assert len(client.list_objects_v2(Bucket=bucket)["Contents"]) == 2


@pytest.mark.parametrize("damage", ["archive", "metadata", "files", "missing"])
def test_minio_corruption_keeps_database_draft_and_audit_unchanged(
    pg_environment, minio_storage, damage
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(sessions, storage, context)
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    if damage == "missing":
        client.delete_object(Bucket=bucket, Key=original.stored.object_key)
    elif damage == "files":
        with sessions.begin() as session:
            session.get(SkillDraft, skill_id).files = [
                {"path": "SKILL.md", "size": 1, "sha256": "0" * 64}
            ]
    else:
        response = client.get_object(Bucket=bucket, Key=original.stored.object_key)
        data = response["Body"].read()
        response["Body"].close()
        metadata = response["Metadata"]
        if damage == "archive":
            data = data[:-1] + bytes([data[-1] ^ 1])
        else:
            metadata["package-digest"] = "0" * 64
        client.put_object(
            Bucket=bucket, Key=original.stored.object_key, Body=data, Metadata=metadata
        )
    with pytest.raises(ProjectSkillError) as caught:
        ProjectSkillService(sessions).save_draft(
            context,
            skill_id,
            ProjectSkillDraftUpdate(
                expected_revision=1, content=manifest("s", "Changed")
            ),
        )
    assert (caught.value.code, caught.value.status_code) == (
        "skill_storage_unavailable",
        503,
    )
    with sessions() as session:
        draft = session.get(SkillDraft, skill_id)
        assert (draft.revision, draft.content) == (1, manifest("s"))
        assert (
            session.scalar(select(AuditEvent).where(AuditEvent.resource_id == skill_id))
            is None
        )


def test_minio_upload_survives_failed_atomic_audit_without_replacing_draft(
    pg_environment, minio_storage
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(sessions, storage, context)
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    with pytest.raises(RuntimeError, match="audit failed"):
        ProjectSkillService(sessions, audit_recorder=FailingAudit()).save_draft(
            context,
            skill_id,
            ProjectSkillDraftUpdate(
                expected_revision=1, content=manifest("s", "Changed")
            ),
        )
    with sessions() as session:
        assert snapshot_draft(session.get(SkillDraft, skill_id)) == original
        assert (
            session.scalar(select(AuditEvent).where(AuditEvent.resource_id == skill_id))
            is None
        )
    assert read_verified_package(storage, original).content == manifest("s")
    assert len(client.list_objects_v2(Bucket=bucket)["Contents"]) == 2


def test_minio_import_all_database_references_read_back(pg_environment, minio_storage):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    result = ProjectSkillService(sessions).import_bundle(context, two_packages(), None)
    assert result.created_count == 2
    with sessions() as session:
        snapshots = [
            snapshot_draft(session.get(SkillDraft, item.skill_id))
            for item in result.items
        ]
    for snapshot, name in zip(snapshots, ("a", "b")):
        assert read_verified_package(storage, snapshot).content == manifest(name)
    assert len(client.list_objects_v2(Bucket=bucket)["Contents"]) == 2


@pytest.mark.parametrize("failure", ["second-object", "second-audit"])
def test_minio_failed_mixed_import_keeps_database_unchanged(
    pg_environment, minio_storage, monkeypatch, failure
):
    sessions, context = pg_environment
    storage, client, bucket = minio_storage
    skill_id = seed_skill(sessions, storage, context, name="original")
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))
    if failure == "second-object":
        put = storage._client.put_object
        calls = 0

        def fail_second(**request):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated second store failure")
            return put(**request)

        monkeypatch.setattr(storage._client, "put_object", fail_second)
    service = ProjectSkillService(
        sessions,
        storage_factory=lambda: storage,
        audit_recorder=FailSecondAudit() if failure == "second-audit" else None,
    )
    with pytest.raises(
        ProjectSkillError if failure == "second-object" else RuntimeError
    ):
        service.import_bundle(context, two_packages(), mixed_manifest(skill_id, 1))
    with sessions() as session:
        assert snapshot_draft(session.get(SkillDraft, skill_id)) == original
        assert session.get(Skill, skill_id).published_version_id is None
        assert (
            len(
                session.scalars(
                    select(Skill).where(Skill.project_id == context.project_id)
                ).all()
            )
            == 1
        )
        assert (
            session.scalar(
                select(AuditEvent).where(AuditEvent.project_id == context.project_id)
            )
            is None
        )
    assert read_verified_package(storage, original).content == manifest("original")
    assert len(client.list_objects_v2(Bucket=bucket)["Contents"]) == (
        2 if failure == "second-object" else 3
    )
