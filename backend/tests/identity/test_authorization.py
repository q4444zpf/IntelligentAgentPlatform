from app.identity.authorization import AuthorizationService
from app.identity.dependencies import require_scoped_permission, require_permission
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


def test_unit_grant_enters_both_targets_but_project_grant_does_not_enter_unit():
    service = AuthorizationService()
    context = context_with_grants(
        PermissionGrant("audit.read", "unit", frozenset(), None),
        PermissionGrant("workflow.read", "project", frozenset({"project-1"}), None),
    )
    assert service.allows_entry(context, "audit.read", "unit")
    assert service.allows_entry(context, "audit.read", "current_project")
    assert not service.allows_entry(context, "workflow.read", "unit")


def test_scoped_permission_is_admission_only_and_accepts_project_and_own_grants():
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()
    context = context_with_grants(
        PermissionGrant("audit.read", "project", frozenset({"project-1"}), None),
        PermissionGrant("audit.read", "own", frozenset({"project-1"}), "user-1"),
    )

    @app.middleware("http")
    async def attach(request: Request, call_next):
        request.state.authorization_context = context
        return await call_next(request)

    @app.get("/audit")
    def audit(value=Depends(require_scoped_permission("audit.read"))):
        return {"user": value.user_id}

    assert TestClient(app).get("/audit").json() == {"user": "user-1"}


def test_permission_dependency_has_stable_401_409_403_order():
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()
    current = {"value": None}

    @app.middleware("http")
    async def attach(request: Request, call_next):
        if current["value"] is not None:
            request.state.authorization_context = current["value"]
        return await call_next(request)

    @app.get("/project")
    def project(value=Depends(require_permission("workflow.read", project_required=True))):
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/project").status_code == 401
    current["value"] = context_with_grants(
        PermissionGrant("workflow.read", "unit", frozenset(), None), project=None
    )
    assert client.get("/project").status_code == 409
    current["value"] = context_with_grants(
        PermissionGrant("workflow.read", "unit", frozenset(), None)
    )
    assert client.get("/project").status_code == 403
