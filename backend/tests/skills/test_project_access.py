from dataclasses import FrozenInstanceError

import pytest
from app.identity.schemas import PermissionGrant
from app.skills.project_access import SkillAccess, require_skill_access
from app.skills.project_errors import ProjectSkillError
from app.skills.repository import SkillScope

from backend.tests.skills.project_support import make_context
from backend.tests.skills.project_support import (
    sessions_fixture as _sessions_fixture,  # noqa: F401
)


def assert_project_error(caught, code: str, status_code: int) -> None:
    assert caught.value.code == code
    assert caught.value.status_code == status_code
    assert str(caught.value) == code


def test_missing_authorization_context_requires_authentication(sessions):
    context = make_context("skill.read").model_copy(
        update={"authorization_context": None}
    )
    with sessions() as session, pytest.raises(ProjectSkillError) as caught:
        require_skill_access(session, context, "skill.read")
    assert_project_error(caught, "skill_authentication_required", 401)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", "user-2"),
        ("unit_id", "unit-2"),
        ("project_id", "project-2"),
    ],
)
def test_request_and_authorization_identity_mismatch_requires_authentication(
    sessions,
    field,
    value,
):
    context = make_context("skill.read").model_copy(update={field: value})
    with sessions() as session, pytest.raises(ProjectSkillError) as caught:
        require_skill_access(session, context, "skill.read")
    assert_project_error(caught, "skill_authentication_required", 401)


@pytest.mark.parametrize(
    "project_id",
    ["", "project-missing", "project-3", "project-inactive"],
)
def test_invalid_current_project_is_rejected(sessions, project_id):
    context = make_context("skill.read", project_id=project_id)
    with sessions() as session, pytest.raises(ProjectSkillError) as caught:
        require_skill_access(session, context, "skill.read")
    assert_project_error(caught, "skill_project_required", 403)


def test_missing_manage_grant_is_denied(sessions):
    context = make_context("skill.read")
    with sessions() as session, pytest.raises(ProjectSkillError) as caught:
        require_skill_access(session, context, "skill.manage")
    assert_project_error(caught, "skill_permission_denied", 403)


def test_grant_for_another_project_is_denied(sessions):
    context = make_context("skill.read")
    authorization = context.authorization_context.model_copy(
        update={
            "grants": (
                PermissionGrant(
                    "skill.read",
                    "project",
                    frozenset({"project-2"}),
                    None,
                ),
            )
        }
    )
    context = context.model_copy(update={"authorization_context": authorization})
    with sessions() as session, pytest.raises(ProjectSkillError) as caught:
        require_skill_access(session, context, "skill.read")
    assert_project_error(caught, "skill_permission_denied", 403)


@pytest.mark.parametrize(
    "data_scope",
    ["unit", "project", "assigned_projects", "custom_projects"],
)
def test_non_own_scope_allows_all_project_owners(sessions, data_scope):
    context = make_context("skill.read", data_scope=data_scope)
    with sessions() as session:
        access = require_skill_access(session, context, "skill.read")
    assert access == SkillAccess(SkillScope("unit-1", "project-1"), None)


def test_own_scope_returns_only_granted_owner(sessions):
    context = make_context("skill.read", data_scope="own")
    with sessions() as session:
        access = require_skill_access(session, context, "skill.read")
    assert access.scope == SkillScope("unit-1", "project-1")
    assert access.owner_ids == frozenset({"user-1"})


def test_own_scope_uses_explicit_grant_owner(sessions):
    context = make_context("skill.read", data_scope="own")
    authorization = context.authorization_context.model_copy(
        update={
            "grants": (
                PermissionGrant(
                    "skill.read",
                    "own",
                    frozenset({"project-1"}),
                    "user-2",
                ),
            )
        }
    )
    context = context.model_copy(update={"authorization_context": authorization})
    with sessions() as session:
        access = require_skill_access(session, context, "skill.read")
    assert access.owner_ids == frozenset({"user-2"})


def test_mixed_valid_scopes_allow_all_project_owners(sessions):
    context = make_context("skill.read", data_scope="own")
    authorization = context.authorization_context.model_copy(
        update={
            "grants": (
                *context.authorization_context.grants,
                PermissionGrant(
                    "skill.read",
                    "custom_projects",
                    frozenset({"project-1"}),
                    None,
                ),
            )
        }
    )
    context = context.model_copy(update={"authorization_context": authorization})
    with sessions() as session:
        access = require_skill_access(session, context, "skill.read")
    assert access.owner_ids is None


def test_skill_access_is_frozen():
    access = SkillAccess(SkillScope("unit-1", "project-1"), None)
    with pytest.raises(FrozenInstanceError):
        access.owner_ids = frozenset({"user-1"})
