import pytest

from app.identity.schemas import AuthorizationContext, PermissionGrant


@pytest.fixture
def authorization_context_factory():
    def build(*grants, user_id="user-1", unit_id="unit-1", project_id="project-1", roles=("viewer",)):
        return AuthorizationContext(
            session_id="test-session",
            user_id=user_id,
            unit_id=unit_id,
            current_project_id=project_id,
            auth_method="dev_test",
            authorization_version=1,
            role_codes=tuple(roles),
            grants=tuple(grants),
        )

    return build


@pytest.fixture
def authenticated_unit_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("platform.read", "unit", frozenset(), None), project_id=None
    )


@pytest.fixture
def project_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("workflow.read", "project", frozenset({"project-1"}), None)
    )


@pytest.fixture
def unit_administrator_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("agent.manage", "unit", frozenset(), None), roles=("unit_admin",)
    )


@pytest.fixture
def project_administrator_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("agent.manage", "project", frozenset({"project-1"}), None),
        roles=("project_admin",),
    )


@pytest.fixture
def auditor_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("audit.read", "unit", frozenset(), None), roles=("unit_auditor",)
    )


@pytest.fixture
def viewer_context(authorization_context_factory):
    return authorization_context_factory(
        PermissionGrant("dashboard.read", "unit", frozenset(), None), roles=("viewer",)
    )
