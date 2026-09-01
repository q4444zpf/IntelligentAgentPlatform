from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
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


def role_id(code: str = "unit_admin", unit_id: str = "unit-1") -> str:
    with app.dependency_overrides[get_session]() as session:
        role = session.scalar(select(Role).where(Role.unit_id == unit_id, Role.code == code))
        assert role is not None
        return role.id


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
        json={"display_name": "Duplicate", "email": "ALICE@EXAMPLE.COM", "role_ids": [role_id()]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "邮箱已存在"


def test_admin_cannot_create_case_insensitive_duplicate_display_name():
    client = build_client()
    response = client.post('/api/identity/users', headers=headers(), json={'display_name': 'alice', 'email': 'unique@example.com', 'role_ids': [role_id()]})
    assert response.status_code == 409
    assert response.json()['detail'] == '用户名已存在'


def test_admin_create_user_requires_at_least_one_unit_role():
    client = build_client()
    missing = client.post(
        "/api/identity/users",
        headers=headers(),
        json={"display_name": "Missing Role", "email": "missing-role@example.test"},
    )
    empty = client.post(
        "/api/identity/users",
        headers=headers(),
        json={"display_name": "Empty Role", "email": "empty-role@example.test", "role_ids": []},
    )
    assert missing.status_code == 422
    assert empty.status_code == 422


def test_admin_create_user_atomically_assigns_selected_unit_roles():
    client = build_client()
    selected_role_id = role_id()
    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={
            "display_name": "Role Bound User",
            "email": "role-bound@example.test",
            "initial_password": "Initial-password-123",
            "role_ids": [selected_role_id],
        },
    )
    assert response.status_code == 201
    user_id = response.json()["id"]
    with app.dependency_overrides[get_session]() as session:
        bindings = session.scalars(
            select(UnitMembershipRole).where(UnitMembershipRole.user_id == user_id)
        ).all()
        assert [binding.role_id for binding in bindings] == [selected_role_id]
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == user_id,
            AuditEvent.action == "identity.user.created",
        )) == 1


def test_admin_create_user_rejects_invalid_unit_role_choices_without_partial_data():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        project_role = session.scalar(select(Role).where(
            Role.unit_id == "unit-1", Role.scope_type == "project",
        ))
        assert project_role is not None
        inactive_role = Role(
            id="inactive-unit-role", code="inactive_unit_role", name="Inactive",
            scope_type="unit", unit_id="unit-1", built_in=False, status="inactive",
        )
        outside_role = Role(
            id="outside-unit-role", code="outside_unit_role", name="Outside",
            scope_type="unit", unit_id="unit-2", built_in=False, status="active",
        )
        session.add_all([inactive_role, outside_role])
        session.commit()
        invalid_role_ids = [project_role.id, inactive_role.id, outside_role.id]

    for index, invalid_role_id in enumerate(invalid_role_ids):
        email = f"invalid-role-{index}@example.test"
        response = client.post(
            "/api/identity/users", headers=headers(),
            json={"display_name": f"Invalid Role {index}", "email": email, "role_ids": [invalid_role_id]},
        )
        assert response.status_code == 422
        with app.dependency_overrides[get_session]() as session:
            assert session.scalar(select(User).where(User.email == email)) is None


def test_admin_can_delete_another_user():
    client = build_client()
    created = client.post('/api/identity/users', headers=headers(), json={'display_name': 'Temporary User', 'email': 'temporary@example.com', 'role_ids': [role_id()]})
    assert created.status_code == 201
    deleted = client.delete(f"/api/identity/users/{created.json()['id']}", headers=headers())
    assert deleted.status_code == 200
    assert deleted.json()['deleted'] is True


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
            "role_ids": [role_id()],
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


def test_admin_requires_email_when_creating_local_password_credentials():
    client = build_client()

    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={
            "display_name": "Local User Without Email",
            "initial_password": "Initial-password-123",
            "role_ids": [role_id()],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "本地账号必须提供邮箱"


def test_admin_create_local_user_without_password_marks_invitation_pending():
    client = build_client()

    response = client.post(
        "/api/identity/users",
        headers=headers(),
        json={
            "display_name": "Invited User",
            "email": "invited@example.com",
            "invite": True,
            "role_ids": [role_id()],
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


def test_updating_user_profile_keeps_existing_session_authorization_valid():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        now = datetime.now(timezone.utc)
        session.add(AuthSession(
            id='profile-session', session_token_hash=_hash('profile-token'), user_id='user-1',
            unit_id='unit-1', current_project_id='project-1', auth_method='dev_test',
            csrf_secret_encrypted={'ciphertext': 'csrf'}, provider_tokens_encrypted=None,
            provider_sid=None, authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30), absolute_expires_at=now + timedelta(hours=1),
            last_seen_at=now,
        ))
        session.commit()
    client.cookies.set(SESSION_COOKIE, 'profile-token')

    response = client.patch(
        '/api/identity/users/user-1',
        headers={'Origin': 'http://testserver', 'X-CSRF-Token': 'csrf'},
        json={'display_name': 'Alice Updated', 'email': 'alice.updated@example.com', 'role_ids': [role_id()]},
    )

    assert response.status_code == 200
    with app.dependency_overrides[get_session]() as session:
        user = session.get(User, 'user-1')
        auth = session.get(AuthSession, 'profile-session')
        assert user.display_name == 'Alice Updated'
        assert user.authorization_version == 1
        assert auth.revoked_at is None


def test_update_user_replaces_unit_roles_and_revokes_session_when_roles_change():
    client = build_client()
    now = datetime.now(timezone.utc)
    with app.dependency_overrides[get_session]() as session:
        second_role = Role(
            id="second-unit-role", code="second_unit_role", name="Second Unit Role",
            scope_type="unit", unit_id="unit-1", built_in=False, status="active",
        )
        session.add(second_role)
        session.add(AuthSession(
            id="role-edit-session", session_token_hash=_hash("role-edit-token"),
            user_id="user-1", unit_id="unit-1", current_project_id="project-1",
            auth_method="dev_test", csrf_secret_encrypted={"ciphertext": "csrf"},
            provider_tokens_encrypted=None, provider_sid=None, authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(hours=1), last_seen_at=now,
        ))
        session.commit()

    response = client.patch(
        "/api/identity/users/user-1", headers=headers(),
        json={
            "display_name": "Alice Updated", "email": "alice.updated@example.test",
            "role_ids": ["second-unit-role"],
        },
    )

    assert response.status_code == 200
    with app.dependency_overrides[get_session]() as session:
        user = session.get(User, "user-1")
        auth = session.get(AuthSession, "role-edit-session")
        bindings = session.scalars(select(UnitMembershipRole).where(
            UnitMembershipRole.user_id == "user-1",
            UnitMembershipRole.unit_id == "unit-1",
        )).all()
        assert {binding.role_id for binding in bindings} == {"second-unit-role"}
        assert user.authorization_version == 2
        assert auth.revoked_at is not None
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == "user-1",
            AuditEvent.action == "identity.user.updated",
        )) == 1


def test_update_user_keeps_session_when_profile_changes_but_roles_do_not():
    client = build_client()
    now = datetime.now(timezone.utc)
    current_role_id = role_id()
    with app.dependency_overrides[get_session]() as session:
        session.add(AuthSession(
            id="profile-edit-session", session_token_hash=_hash("profile-edit-token"),
            user_id="user-1", unit_id="unit-1", current_project_id="project-1",
            auth_method="dev_test", csrf_secret_encrypted={"ciphertext": "csrf"},
            provider_tokens_encrypted=None, provider_sid=None, authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(hours=1), last_seen_at=now,
        ))
        session.commit()

    response = client.patch(
        "/api/identity/users/user-1", headers=headers(),
        json={
            "display_name": "Alice Profile", "email": "alice.profile@example.test",
            "role_ids": [current_role_id],
        },
    )

    assert response.status_code == 200
    with app.dependency_overrides[get_session]() as session:
        user = session.get(User, "user-1")
        auth = session.get(AuthSession, "profile-edit-session")
        assert user.display_name == "Alice Profile"
        assert user.authorization_version == 1
        assert auth.revoked_at is None


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


def test_remove_role_rejects_the_users_last_unit_role():
    client = build_client()
    active_role_id = role_id()
    with app.dependency_overrides[get_session]() as session:
        inactive_role = Role(
            id="inactive-retained-role", code="inactive_retained_role", name="Inactive retained role",
            scope_type="unit", unit_id="unit-1", built_in=False, status="inactive",
        )
        session.add(inactive_role)
        session.add(UnitMembershipRole(
            id="inactive-retained-binding", user_id="user-1", unit_id="unit-1",
            role_id=inactive_role.id, scope_type="unit",
        ))
        session.commit()

    response = client.request(
        "DELETE", "/api/identity/users/user-1/roles",
        headers=headers(), json={"role_id": active_role_id},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "用户必须至少保留一个单位角色"
    with app.dependency_overrides[get_session]() as session:
        assert session.get(UnitMembershipRole, "umr-1") is not None


def test_replace_unit_roles_rejects_an_empty_list_but_project_roles_can_be_cleared():
    client = build_client()

    unit_response = client.put(
        "/api/identity/users/user-1/roles", headers=headers(), json={"role_ids": []},
    )
    project_response = client.put(
        "/api/identity/users/user-1/roles", headers=headers(),
        json={"role_ids": [], "project_id": "project-1"},
    )

    assert unit_response.status_code == 422
    assert unit_response.json()["detail"] == "用户必须至少保留一个单位角色"
    assert project_response.status_code == 200


def test_assign_role_rejects_an_inactive_unit_role():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        session.add(Role(
            id="inactive-assignment-role", code="inactive_assignment_role", name="Inactive assignment",
            scope_type="unit", unit_id="unit-1", built_in=False, status="inactive",
        ))
        session.commit()

    response = client.post(
        "/api/identity/users/user-1/roles", headers=headers(),
        json={"role_id": "inactive-assignment-role"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "请选择当前单位的有效单位角色"


def test_replace_roles_rejects_an_inactive_unit_role():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        session.add(Role(
            id="inactive-replacement-role", code="inactive_replacement_role", name="Inactive replacement",
            scope_type="unit", unit_id="unit-1", built_in=False, status="inactive",
        ))
        session.commit()

    response = client.put(
        "/api/identity/users/user-1/roles", headers=headers(),
        json={"role_ids": ["inactive-replacement-role"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "请选择当前单位的有效单位角色"


def test_delete_custom_role_rejects_removing_a_users_last_unit_role():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        user = User(
            id="last-role-user", display_name="Last Role User",
            email="last-role@example.test", status="active", authorization_version=1,
        )
        role = Role(
            id="last-custom-role", code="last_custom_role", name="Last Custom Role",
            scope_type="unit", unit_id="unit-1", built_in=False, status="active",
        )
        inactive_role = Role(
            id="inactive-last-custom-role", code="inactive_last_custom_role", name="Inactive last custom role",
            scope_type="unit", unit_id="unit-1", built_in=False, status="inactive",
        )
        session.add_all([user, role, inactive_role])
        session.add(UnitMembership(
            id="last-role-membership", user_id=user.id, unit_id="unit-1", status="active",
        ))
        session.add(UnitMembershipRole(
            id="last-role-binding", user_id=user.id, unit_id="unit-1",
            role_id=role.id, scope_type="unit",
        ))
        session.add(UnitMembershipRole(
            id="inactive-last-role-binding", user_id=user.id, unit_id="unit-1",
            role_id=inactive_role.id, scope_type="unit",
        ))
        session.commit()

    response = client.delete("/api/identity/roles/last-custom-role", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "该角色是用户的最后一个单位角色，不能删除"
    with app.dependency_overrides[get_session]() as session:
        assert session.get(Role, "last-custom-role") is not None
        assert session.get(UnitMembershipRole, "last-role-binding") is not None


def test_role_scope_isolated_and_built_in_role_cannot_be_deleted():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        platform_or_other = Role(id='other-role', code='other', name='Other', scope_type='unit', unit_id='unit-2', built_in=False, status='active')
        builtin = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.built_in.is_(True)))
        builtin_id = builtin.id
        session.add(platform_or_other); session.commit()
    assert client.post('/api/identity/users/user-1/roles', headers=headers(), json={'role_id': 'other-role'}).status_code == 422
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


def test_admin_can_update_project_name_within_unit():
    client = build_client()
    response = client.patch('/api/identity/projects/project-1', headers=headers(), json={'name': 'Updated Project'})
    assert response.status_code == 200
    assert response.json()['name'] == 'Updated Project'
    with app.dependency_overrides[get_session]() as session:
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == 'identity.project.updated', AuditEvent.resource_id == 'project-1')) is not None


def test_admin_cannot_update_project_from_another_unit():
    client = build_client()
    response = client.patch('/api/identity/projects/project-2', headers=headers(), json={'name': 'Nope'})
    assert response.status_code == 404


def test_admin_can_update_custom_role_name():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        session.add(Role(id='editable-role', code='editable', name='Before', scope_type='unit', unit_id='unit-1', built_in=False, status='active'))
        session.commit()
    response = client.patch('/api/identity/roles/editable-role', headers=headers(), json={'name': 'After'})
    assert response.status_code == 200
    assert response.json()['name'] == 'After'
    with app.dependency_overrides[get_session]() as session:
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == 'identity.role.updated', AuditEvent.resource_id == 'editable-role')) is not None


def test_builtin_role_cannot_be_updated():
    client = build_client()
    with app.dependency_overrides[get_session]() as session:
        role = session.scalar(select(Role).where(Role.unit_id == 'unit-1', Role.built_in.is_(True)))
        role_id = role.id
    response = client.patch(f'/api/identity/roles/{role_id}', headers=headers(), json={'name': 'Changed'})
    assert response.status_code == 409
