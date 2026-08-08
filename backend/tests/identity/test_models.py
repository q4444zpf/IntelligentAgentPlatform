import pytest
from sqlalchemy import UniqueConstraint

from app.identity.models import (
    AuthSession,
    ExternalIdentity,
    ExternalIdentityHistory,
    LocalCredential,
    Menu,
    MenuPermission,
    OidcLoginTransaction,
    Permission,
    Project,
    ProjectMembership,
    ProjectMembershipRole,
    Role,
    RolePermission,
    RolePermissionProject,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
    validate_role_permission_project,
)


MODELS = (
    User,
    ExternalIdentity,
    ExternalIdentityHistory,
    LocalCredential,
    Unit,
    Project,
    UnitMembership,
    ProjectMembership,
    Role,
    Permission,
    RolePermission,
    UnitMembershipRole,
    ProjectMembershipRole,
    RolePermissionProject,
    Menu,
    MenuPermission,
    OidcLoginTransaction,
    AuthSession,
)


def _unique_column_sets(model: type) -> set[tuple[str, ...]]:
    table = model.__table__
    constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    constraints.update(
        (column.name,) for column in table.columns if column.unique
    )
    return constraints


def test_identity_models_map_the_complete_table_catalogue():
    assert {model.__tablename__ for model in MODELS} == {
        "users",
        "external_identities",
        "external_identity_history",
        "local_credentials",
        "units",
        "projects",
        "unit_memberships",
        "project_memberships",
        "roles",
        "permissions",
        "role_permissions",
        "unit_membership_roles",
        "project_membership_roles",
        "role_permission_projects",
        "menus",
        "menu_permissions",
        "oidc_login_transactions",
        "auth_sessions",
    }


def test_composite_foreign_key_targets_are_unique():
    assert {("id", "unit_id"), ("unit_id", "code")} <= _unique_column_sets(Project)
    assert ("user_id", "unit_id") in _unique_column_sets(UnitMembership)
    assert ("id", "scope_type", "unit_id") in _unique_column_sets(Role)
    assert ("id", "unit_id") in _unique_column_sets(RolePermission)


def test_identity_and_transaction_hashes_are_unique():
    assert ("issuer", "subject") in _unique_column_sets(ExternalIdentity)
    assert ("state_hash",) in _unique_column_sets(OidcLoginTransaction)
    assert ("session_token_hash",) in _unique_column_sets(AuthSession)


def test_menu_keys_are_unique_and_route_fields_are_nullable():
    assert ("node_key",) in _unique_column_sets(Menu)
    assert ("route_key",) in _unique_column_sets(Menu)
    assert Menu.__table__.c.route_key.nullable is True
    assert Menu.__table__.c.parent_id.nullable is True
    assert Menu.__table__.c.visibility_target.nullable is True


def test_session_allows_no_selected_project_and_optional_provider_state():
    assert AuthSession.__table__.c.current_project_id.nullable is True
    assert AuthSession.__table__.c.provider_tokens_encrypted.nullable is True
    assert AuthSession.__table__.c.provider_sid.nullable is True
    assert AuthSession.__table__.c.revoked_at.nullable is True
    assert AuthSession.__table__.c.revoke_reason.nullable is True
    assert AuthSession().current_project_id is None


def test_login_transaction_preserves_audit_lifecycle_fields():
    columns = OidcLoginTransaction.__table__.c
    assert columns.state_hash.nullable is False
    assert columns.nonce_hash.nullable is False
    assert columns.browser_correlation_hash.nullable is False
    assert columns.pkce_verifier_encrypted.nullable is False
    assert columns.issuer.nullable is False
    assert columns.client_id.nullable is False
    assert columns.redirect_uri.nullable is False
    assert columns.return_to.nullable is False
    assert columns.expires_at.nullable is False
    assert columns.consumed_at.nullable is True


def test_custom_project_service_boundary_checks_scope_and_unit():
    project = Project(id="p1", unit_id="unit-a")
    custom_grant = RolePermission(data_scope="custom_projects", unit_id="unit-a")
    validate_role_permission_project(custom_grant, project)

    with pytest.raises(ValueError, match="custom_projects"):
        validate_role_permission_project(
            RolePermission(data_scope="unit", unit_id="unit-a"),
            project,
        )
    with pytest.raises(ValueError, match="same unit"):
        validate_role_permission_project(
            custom_grant,
            Project(id="p2", unit_id="unit-b"),
        )
