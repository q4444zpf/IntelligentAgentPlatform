from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.core.database import get_session
from app.db.base import Base
from app.identity.catalogue import seed_builtin_catalogue
from app.audit.models import AuditEvent
from app.identity.models import Permission, Project, ProjectMembership, ProjectMembershipRole, Role, RolePermission, Unit, UnitMembership, UnitMembershipRole, User
from app.identity.models import AuthSession
from app.identity.models import LocalCredential
from app.identity.passwords import verify_password
from app.identity.auth_router import SESSION_COOKIE, _hash
from datetime import datetime, timedelta, timezone
from app.main import app


def build_client():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        unit = Unit(id='unit-1', code='u1', name='Unit 1', status='active')
        other = Unit(id='unit-2', code='u2', name='Unit 2', status='active')
        user = User(id='user-1', display_name='Alice', email='alice@example.com', status='active')
        hidden = User(id='user-2', display_name='Bob', email='bob@example.com', status='active')
        session.add_all([unit, other, user, hidden]); session.flush()
        project = Project(id='project-1', unit_id=unit.id, code='p1', name='Project 1', status='active')
        hidden_project = Project(id='project-2', unit_id=other.id, code='p2', name='Project 2', status='active')
        session.add_all([project, hidden_project])
        session.add_all([
            UnitMembership(id='um-1', user_id=user.id, unit_id=unit.id, status='active'),
            UnitMembership(id='um-2', user_id=hidden.id, unit_id=other.id, status='active'),
            ProjectMembership(id='pm-1', user_id=user.id, unit_id=unit.id, project_id=project.id, status='active'),
        ])
        seed_builtin_catalogue(session, unit.id); session.flush()
        role = session.query(Role).filter_by(unit_id=unit.id, code='unit_admin').one()
        session.add(UnitMembershipRole(id='umr-1', user_id=user.id, unit_id=unit.id, role_id=role.id, scope_type='unit'))
        session.commit()
    app.state.allow_dev_identity = True
    app.dependency_overrides[get_session] = lambda: factory()
    return TestClient(app)


def headers(role='admin', unit='unit-1'):
    return {'X-User-ID': 'user-1', 'X-Project-ID': 'project-1', 'X-Unit-ID': unit, 'X-User-Role': role}


def test_admin_cookie_session_cannot_become_another_user_with_forged_headers():
    client = build_client()
    # The dev identity is intentionally enabled for this fixture; the cookie still
    # has to be the source of the administrator context when it is present.
    with app.dependency_overrides[get_session]() as session:
        now = datetime.now(timezone.utc)
        token = "admin-cookie-token"
        session.add(AuthSession(
            id="admin-cookie-session", session_token_hash=_hash(token), user_id="user-1",
            unit_id="unit-1", current_project_id="project-1", auth_method="dev_test",
            csrf_secret_encrypted={"ciphertext": "csrf"}, provider_tokens_encrypted=None,
            provider_sid=None, authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=1),
            last_seen_at=now,
        ))
        session.commit()
    client.cookies.set(SESSION_COOKIE, token)

    response = client.get(
        "/api/identity/users",
        headers={"X-User-ID": "attacker", "X-Unit-ID": "other-unit", "X-Project-ID": "other-project", "X-User-Role": "unit_admin"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["user-1"]


def test_admin_headers_are_rejected_when_development_identity_is_disabled():
    previous = app.state.allow_dev_identity
    try:
        app.state.allow_dev_identity = False
        client = TestClient(app)

        response = client.get("/api/identity/users", headers=headers())

        assert response.status_code == 401
    finally:
        app.state.allow_dev_identity = previous


def test_admin_cannot_create_case_insensitive_duplicate_email():
    client = build_client()

    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={"display_name": "Duplicate", "email": "ALICE@EXAMPLE.COM"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "邮箱已存在"


def test_admin_create_local_user_returns_one_time_initial_password_and_stores_only_hash():
    client = build_client()
    initial_password = "Initial-password-123"

    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={
            "display_name": "Local New User",
            "email": "new-local@example.com",
            "initial_password": initial_password,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    user_id = payload["id"]
    assert payload["initial_password"] == initial_password
    assert payload["invitation_status"] == "not_required"
    with app.dependency_overrides[get_session]() as session:
        credential = session.get(LocalCredential, user_id)
        assert credential is not None
        assert credential.password_hash != initial_password
        assert verify_password(initial_password, credential.password_hash)
    listed = client.get("/api/identity/users", headers=headers())
    assert listed.status_code == 200
    listed_user = next(item for item in listed.json() if item["id"] == user_id)
    assert listed_user["initial_password"] is None


def test_admin_create_local_user_without_password_marks_invitation_pending():
    client = build_client()

    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={
            "display_name": "Invited User",
            "email": "invited@example.com",
            "invite": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["initial_password"] is None
    assert payload["invitation_status"] == "pending"
    with app.dependency_overrides[get_session]() as session:
        assert session.scalar(select(LocalCredential).where(LocalCredential.user_id == payload["id"])) is None


def test_admin_lists_nested_users_and_isolates_unit():
    client = build_client()
    response = client.get('/api/identity/users', headers=headers())
    assert response.status_code == 200
    payload = response.json()
    assert [item['id'] for item in payload] == ['user-1']
    assert payload[0]['project_memberships'][0]['project_code'] == 'p1'
    assert payload[0]['role_summaries'][0]['code'] == 'unit_admin'


def test_admin_requires_authentication_and_admin_role():
    client = build_client()
    assert client.get('/api/identity/users').status_code == 401
    assert client.get('/api/identity/users', headers=headers(role='user')).status_code == 403


def test_admin_lists_roles_and_permissions():
    client = build_client()
    assert client.get('/api/identity/roles', headers=headers()).status_code == 200
    permissions = client.get('/api/identity/permissions', headers=headers())
    assert permissions.status_code == 200
    assert any(item['code'] == 'identity.manage' for item in permissions.json())


def test_admin_can_query_user_roles_for_unit_and_project():
    client = build_client()
    response = client.get('/api/identity/users/user-1/roles', headers=headers())
    assert response.status_code == 200
    assert [item['code'] for item in response.json()] == ['unit_admin']

    project_role = None
    with app.dependency_overrides[get_session]() as session:
        project_role = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.scope_type == 'project'))
    assert project_role is not None
    assigned = client.post('/api/identity/users/user-1/roles', headers=headers(), json={'role_id': project_role.id, 'project_id': 'project-1'})
    assert assigned.status_code == 201
    response = client.get('/api/identity/users/user-1/roles?project_id=project-1', headers=headers())
    assert response.status_code == 200
    assert [item['code'] for item in response.json()] == [project_role.code]


def test_role_assignment_revokes_sessions_and_can_be_removed():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        now = datetime.now(timezone.utc)
        session.add(AuthSession(id='role-session', session_token_hash=_hash('role-token'), user_id='user-1', unit_id='unit-1', current_project_id='project-1', auth_method='dev_test', csrf_secret_encrypted={'ciphertext': 'csrf'}, provider_tokens_encrypted=None, provider_sid=None, authorization_version=1, idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=1), last_seen_at=now))
        role = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.scope_type == 'project'))
        role_id = role.id
        session.commit()
    response = client.post('/api/identity/users/user-1/roles', headers=headers(), json={'role_id': role_id, 'project_id': 'project-1'})
    assert response.status_code == 201
    with app.dependency_overrides[get_session]() as session:
        auth = session.get(AuthSession, 'role-session')
        user = session.get(User, 'user-1')
        assert auth.revoked_at is not None
        assert user.authorization_version == 2
    removed = client.request('DELETE', '/api/identity/users/user-1/roles', headers=headers(), json={'role_id': role_id, 'project_id': 'project-1'})
    assert removed.status_code == 200
    assert removed.json()['removed'] is True
    with app.dependency_overrides[get_session]() as session:
        assert session.scalar(select(ProjectMembershipRole).where(ProjectMembershipRole.user_id == 'user-1', ProjectMembershipRole.role_id == role_id)) is None


def test_replace_roles_replaces_only_requested_scope():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        roles = session.scalars(select(Role).where(Role.unit_id == 'unit-1', Role.scope_type == 'unit')).all()
        assert roles
        target_id = roles[0].id
    response = client.put('/api/identity/users/user-1/roles', headers=headers(), json={'role_ids': [target_id]})
    assert response.status_code == 200
    assert [item['role_id'] for item in response.json()] == [target_id]


def test_role_scope_isolated_and_built_in_role_cannot_be_deleted():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        platform_or_other = Role(id='other-role', code='other', name='Other', scope_type='unit', unit_id='unit-2', built_in=False, status='active')
        builtin = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.built_in.is_(True)))
        builtin_id = builtin.id
        session.add(platform_or_other); session.commit()
    assert client.post('/api/identity/users/user-1/roles', headers=headers(), json={'role_id': 'other-role'}).status_code == 404
    assert client.request('DELETE', f'/api/identity/roles/{builtin_id}', headers=headers()).status_code == 409


def test_custom_role_can_be_deleted_after_bindings_are_removed():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        role = Role(id='custom-role', code='custom', name='Custom', scope_type='unit', unit_id='unit-1', built_in=False, status='active')
        session.add(role)
        session.add(UnitMembershipRole(id='custom-binding', user_id='user-1', unit_id='unit-1', role_id=role.id, scope_type='unit'))
        session.commit()
    response = client.request('DELETE', '/api/identity/roles/custom-role', headers=headers())
    assert response.status_code == 200
    with app.dependency_overrides[get_session]() as session:
        assert session.get(Role, 'custom-role') is None
        assert session.get(UnitMembershipRole, 'custom-binding') is None


def test_admin_can_query_role_permissions_with_unit_scope():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        role = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.code == 'unit_admin'))
        assert role is not None
        expected = session.scalars(
            select(RolePermission).where(RolePermission.role_id == role.id).order_by(RolePermission.permission_code)
        ).all()
        assert expected
        role_id = role.id
    response = client.get(f'/api/identity/roles/{role_id}/permissions', headers=headers())
    assert response.status_code == 200
    payload = response.json()
    assert [item['permission_code'] for item in payload] == [item.permission_code for item in expected]
    assert all(item['role_id'] == role_id and item['data_scope'] for item in payload)


def test_admin_can_revoke_custom_role_permission_and_invalidates_bound_users():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        role = Role(id='permission-role', code='permission-role', name='Permission role', scope_type='unit', unit_id='unit-1', built_in=False, status='active')
        session.add(role)
        session.add(UnitMembershipRole(id='permission-binding', user_id='user-1', unit_id='unit-1', role_id=role.id, scope_type='unit'))
        permission = session.scalar(select(Permission).where(Permission.code == 'identity.read'))
        assert permission is not None
        permission_code = permission.code
        grant = RolePermission(id='permission-grant', role_id=role.id, permission_code=permission.code, unit_id='unit-1', data_scope='unit')
        session.add(grant)
        now = datetime.now(timezone.utc)
        session.add(AuthSession(id='permission-session', session_token_hash=_hash('permission-token'), user_id='user-1', unit_id='unit-1', current_project_id='project-1', auth_method='dev_test', csrf_secret_encrypted={'ciphertext': 'csrf'}, provider_tokens_encrypted=None, provider_sid=None, authorization_version=1, idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=1), last_seen_at=now))
        session.commit()
    response = client.delete(f'/api/identity/roles/permission-role/permissions/{permission_code}', headers=headers())
    assert response.status_code == 200
    assert response.json() == {'role_id': 'permission-role', 'permission_code': permission_code, 'removed': True}
    with app.dependency_overrides[get_session]() as session:
        assert session.get(RolePermission, 'permission-grant') is None
        user = session.get(User, 'user-1')
        auth = session.get(AuthSession, 'permission-session')
        assert user.authorization_version == 2
        assert auth.revoked_at is not None
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == 'identity.role_permission.revoked', AuditEvent.resource_id == 'permission-role'))
        assert event is not None


def test_builtin_role_permissions_cannot_be_revoked():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        role = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.code == 'unit_admin'))
        grant = session.scalar(select(RolePermission).where(RolePermission.role_id == role.id))
        assert grant is not None
        role_id, permission_code = role.id, grant.permission_code
    response = client.delete(f'/api/identity/roles/{role_id}/permissions/{permission_code}', headers=headers())
    assert response.status_code == 409
    grant_response = client.post(
        f'/api/identity/roles/{role_id}/permissions',
        headers=headers(),
        json={'permission_code': permission_code, 'data_scope': 'unit'},
    )
    assert grant_response.status_code == 409
