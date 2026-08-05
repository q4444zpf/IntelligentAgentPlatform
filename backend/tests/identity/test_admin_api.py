from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.core.database import get_session
from app.db.base import Base
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import Project, ProjectMembership, Role, Unit, UnitMembership, UnitMembershipRole, User
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
