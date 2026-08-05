from app.identity.authorization import AuthorizationService
from app.identity.schemas import AuthorizationContext, PermissionGrant, ResourceScope


def context_with_grants(*grants, project="project-1"):
    return AuthorizationContext(
        session_id="session-1",
        user_id="user-1",
        unit_id="unit-1",
        current_project_id=project,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("viewer",),
        grants=grants,
    )


def test_grants_do_not_multiply_privileges_between_tuples():
    service = AuthorizationService()
    context = context_with_grants(
        PermissionGrant("agent.run", "own", frozenset({"project-1"}), "user-1"),
        PermissionGrant("agent.read", "unit", frozenset(), None),
    )
    assert service.allows(context, "agent.run", ResourceScope("unit-1", "project-1", "user-1"))
    assert not service.allows(context, "agent.run", ResourceScope("unit-1", "project-1", "user-2"))
    assert service.allows(context, "agent.read", ResourceScope("unit-1", "project-2", "user-2"))
    assert not service.allows(context, "agent.read", ResourceScope("unit-2", "project-2", "user-2"))


def test_entry_capabilities_are_sorted_and_target_specific():
    service = AuthorizationService()
    context = context_with_grants(
        PermissionGrant("z.read", "unit", frozenset(), None),
        PermissionGrant("a.run", "own", frozenset({"project-1"}), "user-1"),
    )
    assert [(item.code, item.target) for item in service.entry_capabilities(context)] == [
        ("a.run", "current_project"),
        ("z.read", "current_project"),
        ("z.read", "unit"),
    ]


def test_project_only_grant_cannot_enter_unit_target():
    service = AuthorizationService()
    context = context_with_grants(
        PermissionGrant("workflow.read", "project", frozenset({"project-1"}), None),
    )
    assert not service.allows_entry(context, "workflow.read", "unit")
    assert service.allows_entry(context, "workflow.read", "current_project")


def test_no_current_project_never_emits_project_capabilities():
    service = AuthorizationService()
    context = context_with_grants(
        PermissionGrant("workflow.read", "unit", frozenset(), None),
        project=None,
    )
    assert [(item.code, item.target) for item in service.entry_capabilities(context)] == [
        ("workflow.read", "unit"),
    ]
