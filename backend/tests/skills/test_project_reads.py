from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.core.request_context import RequestContext
from app.skills.models import Skill, SkillDraft
from app.skills.project_errors import ProjectSkillError
from app.skills.project_service import ProjectSkillService
from app.skills.repository import SkillRepository, SkillScope
from sqlalchemy import event

from backend.tests.skills.project_support import (
    make_context,
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


def assert_error(code: str, status_code: int, operation) -> None:
    with pytest.raises(ProjectSkillError) as caught:
        operation()
    assert caught.value.code == code
    assert caught.value.status_code == status_code
    assert str(caught.value) == code


def test_own_filter_is_applied_before_pagination_and_count(sessions, storage):
    """Catches owner filtering applied after SQL pagination/count."""
    own = make_context("skill.read", data_scope="own")
    other = make_context("skill.read", user_id="user-2")
    seed_skill(sessions, storage, other, name="hidden")
    expected = seed_skill(sessions, storage, own, name="visible")
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)

    page = service.list(own, offset=0, limit=1)

    assert page.total == 1
    assert [item.id for item in page.items] == [expected]


def test_list_projects_only_explicit_summary_and_uses_literal_search(sessions, storage):
    """Catches list DTO leakage and wildcard interpretation in q."""
    context = make_context("skill.read")
    expected = seed_skill(
        sessions, storage, context, name="flow-rate-percent", body="secret body"
    )
    seed_skill(sessions, storage, context, name="flow-rate")
    with sessions() as session:
        session.get(Skill, expected).name = "flow%rate"
        session.commit()
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)

    page = service.list(context, q="%")

    assert page.total == 1
    assert [item.id for item in page.items] == [expected]
    assert set(page.items[0].model_dump()) == {
        "id",
        "name",
        "description",
        "display_version",
        "draft_revision",
        "published_version_id",
        "created_at",
        "updated_at",
    }


def test_read_does_not_construct_storage(sessions, storage):
    """Catches metadata reads contacting object storage."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)

    def forbidden_storage():
        raise AssertionError("Metadata reads must not contact storage")

    service = ProjectSkillService(sessions, storage_factory=forbidden_storage)
    assert service.get(context, skill_id).id == skill_id


def test_summary_updated_at_is_newer_resource_or_draft_time(sessions, storage):
    """Catches summary updated_at projecting only one side of the resource/draft pair."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with sessions() as session:
        skill = session.get(Skill, skill_id)
        draft = session.get(SkillDraft, skill_id)
        skill.updated_at = older
        draft.updated_at = newer
        session.commit()

    summary = ProjectSkillService(sessions).get(context, skill_id)

    assert summary.updated_at.replace(tzinfo=timezone.utc) == newer


def test_draft_and_version_dtos_include_files_without_storage_fields(sessions, storage):
    """Catches omission of file metadata or leakage of object credentials/locations."""
    context = make_context("skill.read")
    skill_id = seed_skill(
        sessions, storage, context, attachments=(("reference.txt", b"reference"),)
    )
    version_id = publish_skill(sessions, context, skill_id)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)

    draft = service.get_draft(context, skill_id)
    version = service.get_version(context, skill_id, version_id)

    assert draft.content.endswith("Initial instructions\n")
    assert {item.path for item in draft.files} == {"SKILL.md", "reference.txt"}
    assert version.content == draft.content
    assert set(draft.model_dump()) == {
        "skill_id",
        "name",
        "description",
        "display_version",
        "revision",
        "content",
        "files",
        "package_digest",
        "updated_at",
    }
    assert "object_key" not in version.model_dump()
    assert "archive_sha256" not in version.model_dump()


def test_version_list_is_descending_and_excludes_large_fields(sessions, storage):
    """Catches ascending history or content/files returned by list."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)
    first_id = publish_skill(sessions, context, skill_id)
    second_id = publish_skill(sessions, context, skill_id, expected_revision=2)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)

    page = service.list_versions(context, skill_id, offset=0, limit=20)

    assert [item.id for item in page.items] == [second_id, first_id]
    assert page.total == 2
    assert "content" not in page.items[0].model_dump()
    assert "files" not in page.items[0].model_dump()


def test_empty_visible_skill_history_is_not_a_missing_skill(sessions, storage):
    """Catches an empty version history incorrectly mapped to 404."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)
    page = ProjectSkillService(sessions, storage_factory=lambda: storage).list_versions(
        context, skill_id
    )
    assert page.total == 0
    assert page.items == []


def test_version_id_owned_by_another_visible_skill_is_not_found(sessions, storage):
    """Catches version lookup by version ID without checking its skill owner."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context, name="first")
    other_id = seed_skill(sessions, storage, context, name="second")
    other_version_id = publish_skill(sessions, context, other_id)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)

    assert_error(
        "skill_not_found",
        404,
        lambda: service.get_version(context, skill_id, other_version_id),
    )


@pytest.mark.parametrize("read_kind", ["summary", "draft", "versions", "version"])
@pytest.mark.parametrize("boundary", ["project", "unit", "owner"])
def test_each_read_hides_resources_outside_scope(
    sessions, storage, read_kind, boundary
):
    """Catches any read path omitting project, unit, or owner scope predicates."""
    visible = make_context("skill.read", data_scope="own")
    if boundary == "project":
        hidden = make_context("skill.read", project_id="project-2")
    elif boundary == "unit":
        hidden = make_context(
            "skill.read", user_id="user-3", unit_id="unit-2", project_id="project-3"
        )
    else:
        hidden = make_context("skill.read", user_id="user-2")
    skill_id = seed_skill(
        sessions, storage, hidden, name=f"hidden-{boundary}-{read_kind}"
    )
    version_id = publish_skill(sessions, hidden, skill_id)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    operations = {
        "summary": lambda: service.get(visible, skill_id),
        "draft": lambda: service.get_draft(visible, skill_id),
        "versions": lambda: service.list_versions(visible, skill_id),
        "version": lambda: service.get_version(visible, skill_id, version_id),
    }
    assert_error("skill_not_found", 404, operations[read_kind])


def test_reads_distinguish_authentication_permission_and_resource_failures(
    sessions, storage
):
    """Catches authorization checks performed after resource lookup."""
    allowed = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, allowed)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    missing_identity = RequestContext(
        user_id=allowed.user_id,
        unit_id=allowed.unit_id,
        project_id=allowed.project_id,
        authorization_context=None,
    )
    forbidden = make_context()

    assert_error(
        "skill_authentication_required",
        401,
        lambda: service.get(missing_identity, skill_id),
    )
    assert_error(
        "skill_permission_denied", 403, lambda: service.get(forbidden, skill_id)
    )
    assert_error("skill_not_found", 404, lambda: service.get(allowed, str(uuid4())))


def test_list_query_selects_one_summary_projection_without_n_plus_one(
    sessions, storage
):
    """Catches loading content/files or issuing a draft query per listed skill."""
    context = make_context("skill.read")
    seed_skill(sessions, storage, context, name="one", body="classified one")
    seed_skill(sessions, storage, context, name="two", body="classified two")
    statements: list[str] = []
    engine = sessions.kw["bind"]

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe)
    try:
        ProjectSkillService(sessions, storage_factory=lambda: storage).list(context)
    finally:
        event.remove(engine, "before_cursor_execute", observe)

    draft_selects = [
        sql
        for sql in statements
        if sql.lstrip().startswith("select") and "skill_drafts" in sql
    ]
    assert len(draft_selects) == 2  # count + one projected page query
    page_select = next(sql for sql in draft_selects if "limit" in sql)
    assert "skill_drafts.content" not in page_select
    assert "skill_drafts.files" not in page_select


def test_repository_empty_owner_set_matches_no_rows(sessions, storage):
    """Catches an empty owner set being treated as unrestricted access."""
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)
    with sessions() as session:
        repository = SkillRepository(session)
        items, total = repository.list_summaries(
            SkillScope(context.unit_id, context.project_id), owner_ids=frozenset()
        )
        assert items == []
        assert total == 0
        assert (
            repository.get(
                SkillScope(context.unit_id, context.project_id),
                skill_id,
                owner_ids=frozenset(),
            )
            is None
        )
