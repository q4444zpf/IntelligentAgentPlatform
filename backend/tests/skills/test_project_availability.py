import pytest
from app.audit.models import AuditEvent
from app.skills.models import Skill, SkillDraft, SkillVersion
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
from fastapi.testclient import TestClient


def test_availability_api_updates_current_state_without_mutating_published_version(
    sessions, storage
):
    context = make_context("skill.read", "skill.manage")
    with TestClient(make_test_app(sessions, lambda: storage, context=context)) as client:
        created = client.post("/api/project-skills", json={"content": manifest("s")})
        skill_id = created.json()["id"]
        published = client.post(
            f"/api/project-skills/{skill_id}/publish",
            json={"expected_revision": 1},
            headers={"Idempotency-Key": "availability-publish"},
        )
        version_id = published.json()["id"]
        assert published.json()["enabled"] is True
        assert not {"object_key", "archive_sha256", "size_bytes"} & published.json().keys()
        with sessions() as session:
            original_version = session.get(SkillVersion, version_id)
            original_storage = (
                original_version.object_key,
                original_version.archive_sha256,
                original_version.size_bytes,
            )
        disabled = client.patch(
            f"/api/project-skills/{skill_id}/availability",
            json={"enabled": False, "expected_revision": 2},
        )
        stale = client.patch(
            f"/api/project-skills/{skill_id}/availability",
            json={"enabled": True, "expected_revision": 2},
        )
        summary = client.get(f"/api/project-skills/{skill_id}")
        version = client.get(f"/api/project-skills/{skill_id}/versions/{version_id}")
        reenabled = client.patch(
            f"/api/project-skills/{skill_id}/availability",
            json={"enabled": True, "expected_revision": 3},
        )

    assert disabled.status_code == 200
    assert disabled.headers["cache-control"] == "no-store"
    assert disabled.json()["enabled"] is False
    assert disabled.json()["draft_revision"] == 3
    assert stale.status_code == 409
    assert stale.json() == {"detail": "skill_revision_conflict"}
    assert summary.json()["enabled"] is False
    assert version.status_code == 200
    assert version.json()["enabled"] is False
    assert not {"object_key", "archive_sha256", "size_bytes"} & version.json().keys()
    assert reenabled.json()["enabled"] is True
    assert reenabled.json()["draft_revision"] == 4
    with sessions() as session:
        skill = session.get(Skill, skill_id)
        draft = session.get(SkillDraft, skill_id)
        published_version = session.get(SkillVersion, version_id)
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.resource_id == skill_id,
            AuditEvent.action == "skill.availability.update",
        )))

    assert skill.enabled is True
    assert draft.revision == 4
    assert published_version.content == manifest("s")
    assert published_version.package_digest == published.json()["package_digest"]
    assert (
        published_version.object_key,
        published_version.archive_sha256,
        published_version.size_bytes,
    ) == original_storage
    assert len(events) == 2
    assert {event.metadata_json["enabled"] for event in events} == {False, True}


@pytest.mark.parametrize(
    ("context", "status", "detail"),
    [
        (make_context("skill.read"), 403, "skill_permission_denied"),
        (make_context("skill.manage", project_id="project-2"), 404, "skill_not_found"),
    ],
)
def test_availability_api_requires_skill_management_access_in_the_target_scope(
    sessions, storage, context, status, detail
):
    skill_id = seed_skill(sessions, storage, make_context("skill.manage"))
    with TestClient(make_test_app(sessions, lambda: storage, context=context)) as client:
        response = client.patch(
            f"/api/project-skills/{skill_id}/availability",
            json={"enabled": False, "expected_revision": 1},
        )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    with sessions() as session:
        assert session.get(Skill, skill_id).enabled is True
