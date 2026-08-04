from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.db.base import Base
from app.identity import bootstrap as bootstrap_module
from app.identity.bootstrap import (
    BootstrapRequest,
    _request_from_json,
    _windows_acl_is_protected,
    bootstrap_initial_unit_admin,
)
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    ExternalIdentity,
    Menu,
    MenuPermission,
    Permission,
    Project,
    ProjectMembership,
    Role,
    RolePermission,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
)


PERMISSION_CODES = (
    "platform.read", "dashboard.read", "project.read", "project.manage",
    "project.member.manage", "identity.read", "identity.manage", "agent.read",
    "agent.manage", "agent.run", "conversation.read", "conversation.manage",
    "workflow.read", "workflow.manage", "workflow.run", "knowledge.read",
    "knowledge.manage", "knowledge.retrieve", "model.read", "model.manage",
    "model.run", "tool.read", "tool.manage", "tool.invoke", "mcp.read",
    "mcp.manage", "mcp.sync", "skill.read", "skill.manage", "skill.invoke",
    "collaboration.read", "collaboration.manage", "collaboration.run",
    "prompt.read", "prompt.manage", "resource.read", "resource.manage",
    "resource.publish", "resource.review", "artifact.read", "artifact.manage",
    "approval.read", "approval.manage", "policy.read", "policy.manage",
    "credential.read", "credential.manage", "settings.read", "settings.manage",
    "audit.read", "sandbox.read", "integration.read", "integration.manage",
)

ROLE_PERMISSION_CODES = {
    "project_admin": (
        "dashboard.read", "project.read", "project.manage", "project.member.manage",
        "agent.read", "agent.manage", "agent.run", "conversation.read",
        "conversation.manage", "workflow.read", "workflow.manage", "workflow.run",
        "knowledge.read", "knowledge.manage", "knowledge.retrieve", "model.read",
        "model.manage", "model.run", "tool.read", "tool.invoke", "mcp.read",
        "skill.read", "skill.invoke", "collaboration.read", "collaboration.manage",
        "collaboration.run", "prompt.read", "prompt.manage", "resource.read",
        "resource.manage", "resource.publish", "artifact.read", "artifact.manage",
        "approval.read", "integration.read",
    ),
    "business_operator": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run", "knowledge.read",
        "knowledge.retrieve", "model.read", "model.run", "tool.read", "tool.invoke",
        "skill.read", "skill.invoke", "collaboration.read", "collaboration.run",
        "resource.read", "artifact.read", "approval.read",
    ),
    "model_expert": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run", "knowledge.read",
        "knowledge.manage", "knowledge.retrieve", "model.read", "model.run",
        "tool.read", "tool.invoke", "skill.read", "skill.invoke", "prompt.read",
        "prompt.manage", "resource.read", "resource.manage", "artifact.read",
        "artifact.manage",
    ),
    "unit_auditor": (
        "platform.read", "dashboard.read", "project.read", "identity.read",
        "agent.read", "conversation.read", "workflow.read", "knowledge.read",
        "model.read", "tool.read", "mcp.read", "skill.read", "collaboration.read",
        "prompt.read", "resource.read", "artifact.read", "approval.read",
        "policy.read", "audit.read", "sandbox.read", "integration.read",
        "settings.read",
    ),
    "viewer": (
        "dashboard.read", "project.read", "agent.read", "conversation.read",
        "workflow.read", "knowledge.read", "model.read", "tool.read", "skill.read",
        "collaboration.read", "resource.read", "artifact.read",
    ),
}

MENU_CONTRACT = {
    "dashboard": ("platform.read", "unit", False),
    "chat": ("agent.run", "unit", True),
    "agent-manage": ("agent.manage", "unit", False),
    "collaboration": ("collaboration.read", "current_project", True),
    "workflow": ("workflow.read", "current_project", True),
    "llm": ("model.manage", "unit", False),
    "mcp": ("mcp.manage", "unit", False),
    "skill": ("skill.manage", "unit", False),
    "tools": ("tool.manage", "unit", False),
    "knowledge": ("knowledge.read", "current_project", True),
    "prompt": ("prompt.read", "current_project", True),
    "external-agents": ("integration.read", "unit", False),
    "my-agents": ("resource.read", "current_project", True),
    "my-mcp": ("resource.read", "current_project", True),
    "my-skills": ("resource.read", "current_project", True),
    "my-publish": ("resource.publish", "current_project", True),
    "project-resources": ("resource.read", "current_project", True),
    "hydraulic-topology": ("resource.read", "current_project", True),
    "unit-resources": ("resource.read", "unit", False),
    "public-agents": ("resource.read", "current_project", True),
    "public-mcp": ("resource.read", "current_project", True),
    "public-skills": ("resource.read", "current_project", True),
    "publish-review": ("resource.review", "current_project", True),
    "runs": ("conversation.read", "current_project", True),
    "async-tasks": ("workflow.read", "current_project", True),
    "sandbox": ("sandbox.read", "unit", False),
    "artifacts": ("artifact.read", "current_project", True),
    "approvals": ("approval.read", "current_project", True),
    "policies": ("policy.read", "unit", False),
    "credentials": ("credential.read", "unit", False),
    "audit": ("audit.read", "unit", False),
    "users": ("identity.read", "unit", False),
    "unit-projects": ("project.read", "unit", False),
    "roles": ("identity.read", "unit", False),
    "integration": ("integration.read", "unit", False),
    "settings": ("settings.read", "unit", False),
}

MENU_PARENTS = {
    "agents": ("agent-manage", "collaboration", "workflow"),
    "capabilities": (
        "llm", "mcp", "skill", "tools", "knowledge", "prompt", "external-agents",
    ),
    "resources": (
        "my-agents", "my-mcp", "my-skills", "my-publish", "project-resources",
        "hydraulic-topology", "unit-resources", "public-agents", "public-mcp",
        "public-skills", "publish-review",
    ),
    "operations": ("runs", "async-tasks", "sandbox", "artifacts"),
    "security": ("approvals", "policies", "credentials", "audit"),
    "system": ("users", "unit-projects", "roles", "integration", "settings"),
}


class CountingSession(Session):
    commit_calls = 0
    rollback_calls = 0
    user_lock_calls = 0

    def execute(self, statement, *args, **kwargs):
        if getattr(statement, "_for_update_arg", None) is not None:
            self.user_lock_calls += 1
        return super().execute(statement, *args, **kwargs)

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=CountingSession, expire_on_commit=False)


def make_request(index=1, **overrides):
    values = {
        "unit_code": f"unit-{index}",
        "unit_name": f"测试单位 {index}",
        "user_display_name": "初始管理员",
        "issuer": "https://id.example/issuer/",
        "subject": f"Subject-{index}",
        "initial_project_code": f"project-{index}",
        "initial_project_name": f"初始项目 {index}",
    }
    values.update(overrides)
    return BootstrapRequest(**values)


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("import_target", ["app.db.base", "app.identity.models"])
def test_fresh_import_registers_identity_invariants_without_a_cycle(import_target):
    backend_dir = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(backend_dir)
    probe = f"import importlib\nimportlib.import_module({import_target!r})\n" + r'''
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.identity.models import Permission, Role, Unit, UnitMembership, User

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
with Session(engine) as session:
    unit_1 = Unit(id="unit-1", code="unit-1", name="Unit 1", status="active")
    unit_2 = Unit(id="unit-2", code="unit-2", name="Unit 2", status="active")
    user = User(id="user-1", display_name="User", status="active")
    role = Role(
        id="role-1", unit_id="unit-1", code="unit_admin", name="Admin",
        scope_type="unit", built_in=True, status="active",
    )
    permission = Permission(
        id="permission-1", code="platform.read", resource="platform",
        action="read", risk_level="medium", status="active",
    )
    session.add_all([unit_1, unit_2, user, role, permission])
    session.add(UnitMembership(
        id="membership-1", user_id="user-1", unit_id="unit-1", status="active",
    ))
    session.commit()

    role.built_in = False
    session.delete(role)
    try:
        session.commit()
    except ValueError:
        session.rollback()
    else:
        raise SystemExit("built-in role guard was not registered")

    session.add(UnitMembership(
        id="membership-2", user_id="user-1", unit_id="unit-2", status="active",
    ))
    try:
        session.commit()
    except ValueError:
        session.rollback()
    else:
        raise SystemExit("active membership guard was not registered")
'''

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=backend_dir.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_seed_is_idempotent_and_uses_exact_role_grants(session_factory):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()

        seed_builtin_catalogue(session, unit.id)
        seed_builtin_catalogue(session, unit.id)
        session.flush()

        assert _count(session, Permission) == len(PERMISSION_CODES)
        assert _count(session, Role) == 6
        assert _count(session, Menu) == 42
        assert _count(session, MenuPermission) == 36
        assert set(session.scalars(select(Permission.code))) == set(PERMISSION_CODES)

        roles = {role.code: role for role in session.scalars(select(Role))}
        assert set(roles) == {
            "unit_admin", "project_admin", "business_operator", "model_expert",
            "unit_auditor", "viewer",
        }
        grants = session.scalars(select(RolePermission)).all()
        by_role = {
            code: {
                (grant.permission_code, grant.data_scope)
                for grant in grants
                if grant.role_id == role.id
            }
            for code, role in roles.items()
        }
        assert by_role["unit_admin"] == {(code, "unit") for code in PERMISSION_CODES}
        assert by_role["unit_auditor"] == {
            (code, "unit") for code in ROLE_PERMISSION_CODES["unit_auditor"]
        }
        for code in ("project_admin", "model_expert", "viewer"):
            assert by_role[code] == {
                (permission, "project")
                for permission in ROLE_PERMISSION_CODES[code]
            }
        assert by_role["business_operator"] == {
            *((permission, "project") for permission in ROLE_PERMISSION_CODES["business_operator"]),
            ("conversation.manage", "own"),
        }


def test_seed_removes_unexpected_builtin_and_menu_grants_but_keeps_custom_role(
    session_factory,
):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.flush()

        roles = {role.code: role for role in session.scalars(select(Role))}
        menus = {menu.node_key: menu for menu in session.scalars(select(Menu))}
        custom_role = Role(
            code="custom_operator",
            name="自定义操作员",
            scope_type="project",
            unit_id=unit.id,
            built_in=False,
            status="active",
        )
        session.add(custom_role)
        session.flush()
        custom_grant = RolePermission(
            role_id=custom_role.id,
            permission_code="identity.manage",
            unit_id=unit.id,
            data_scope="unit",
        )
        unexpected_viewer_grant = RolePermission(
            role_id=roles["viewer"].id,
            permission_code="identity.manage",
            unit_id=unit.id,
            data_scope="unit",
        )
        extra_dashboard_mapping = MenuPermission(
            menu_id=menus["dashboard"].id,
            permission_code="dashboard.read",
        )
        extra_chat_mapping = MenuPermission(
            menu_id=menus["chat"].id,
            permission_code="identity.read",
        )
        session.add_all(
            [
                custom_grant,
                unexpected_viewer_grant,
                extra_dashboard_mapping,
                extra_chat_mapping,
            ]
        )
        session.flush()

        seed_builtin_catalogue(session, unit.id)
        session.flush()

        assert session.get(RolePermission, custom_grant.id) is not None
        assert session.get(RolePermission, unexpected_viewer_grant.id) is None
        assert session.get(MenuPermission, extra_dashboard_mapping.id) is None
        assert session.get(MenuPermission, extra_chat_mapping.id) is None


def test_seed_rejects_an_unexpected_builtin_role_code(session_factory):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        session.add(
            Role(
                code="unexpected_builtin",
                name="未登记内置角色",
                scope_type="unit",
                unit_id=unit.id,
                built_in=True,
                status="active",
            )
        )
        session.flush()

        with pytest.raises(ValueError, match="unexpected built-in role"):
            seed_builtin_catalogue(session, unit.id)


def test_seed_uses_closed_menu_catalogue_hierarchy_and_order(session_factory):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.flush()

        menus = {menu.node_key: menu for menu in session.scalars(select(Menu))}
        mappings = {
            menus_by_id.node_key: mapping.permission_code
            for mappings_row in session.execute(
                select(Menu, MenuPermission).join(MenuPermission, Menu.id == MenuPermission.menu_id)
            )
            for menus_by_id, mapping in (mappings_row,)
        }
        assert set(menus) == {"chat", "dashboard", *MENU_PARENTS, *MENU_CONTRACT}
        assert set(mappings) == set(MENU_CONTRACT)
        for route_key, (permission, target, requires_project) in MENU_CONTRACT.items():
            menu = menus[route_key]
            assert menu.route_key == route_key
            assert menu.visibility_target == target
            assert menu.requires_current_project is requires_project
            assert mappings[route_key] == permission

        assert [(menus[key].title, menus[key].sort_order) for key in ("chat", "dashboard")] == [
            ("AI 对话", 0), ("工作台", 1),
        ]
        assert [(menus[key].title, menus[key].sort_order) for key in MENU_PARENTS] == [
            ("智能体", 2), ("能力", 3), ("资源", 4),
            ("运行", 5), ("安全", 6), ("系统", 7),
        ]
        for parent_key, children in MENU_PARENTS.items():
            assert [
                menu.node_key
                for menu in sorted(
                    (value for value in menus.values() if value.parent_id == menus[parent_key].id),
                    key=lambda value: value.sort_order,
                )
            ] == list(children)
        assert menus["unit-resources"].title == "单位资源"
        assert menus["unit-projects"].title == "单位与项目"
        assert all(not hasattr(menu, "path") and not hasattr(menu, "component") for menu in menus.values())


@pytest.mark.parametrize("model_name,operation", [
    ("role", "rename"), ("role", "delete"),
    ("permission", "rename"), ("permission", "delete"),
])
def test_builtin_role_and_permission_codes_cannot_be_renamed_or_deleted(
    session_factory, model_name, operation
):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.commit()
        target = (
            session.scalar(select(Role).where(Role.code == "unit_admin"))
            if model_name == "role"
            else session.scalar(select(Permission).where(Permission.code == "platform.read"))
        )
        if operation == "rename":
            target.code = "renamed"
        else:
            session.delete(target)

        with pytest.raises(ValueError, match="built-in"):
            session.commit()
        session.rollback()


def test_permission_rename_then_delete_cannot_bypass_builtin_guard(session_factory):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.commit()
        permission = session.scalar(
            select(Permission).where(Permission.code == "platform.read")
        )

        permission.code = "renamed"
        session.delete(permission)

        with pytest.raises(ValueError, match="built-in"):
            session.commit()


def test_builtin_false_then_delete_cannot_bypass_role_guard(session_factory):
    with session_factory() as session:
        unit = Unit(code="unit-1", name="测试单位", status="active")
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.commit()
        role = session.scalar(select(Role).where(Role.code == "unit_admin"))

        role.built_in = False
        session.delete(role)

        with pytest.raises(ValueError, match="built-in"):
            session.commit()


@pytest.mark.parametrize(
    "statement",
    [
        update(Role).values(code="bulk_renamed"),
        delete(Role),
        update(Permission).values(code="bulk.renamed"),
        delete(Permission),
    ],
)
def test_orm_bulk_role_and_permission_mutations_fail_closed(
    session_factory, statement
):
    with session_factory() as session:
        with pytest.raises(ValueError, match="bulk"):
            session.execute(statement)


def test_bootstrap_creates_exact_binding_memberships_admin_role_and_redacted_audit(
    session_factory,
):
    request = make_request(
        issuer="https://ID.example/issuer/ ",
        subject=" Subject-A ",
    )
    with session_factory() as session:
        user_id = bootstrap_initial_unit_admin(session, request)
        assert session.commit_calls == 1
        user = session.get(User, user_id)
        identity = session.scalar(select(ExternalIdentity))
        unit = session.scalar(select(Unit))
        project = session.scalar(select(Project))
        audit = session.scalar(select(AuditEvent))

        assert user.display_name == request.user_display_name
        assert user.email is None
        assert user.authorization_version == 2
        assert (identity.issuer, identity.subject) == (request.issuer, request.subject)
        assert identity.user_id == user_id
        assert identity.claims == {}
        assert _count(session, UnitMembership) == 1
        assert _count(session, ProjectMembership) == 1
        assert session.scalar(select(UnitMembership)).status == "active"
        assert session.scalar(select(ProjectMembership)).status == "active"
        assert (project.unit_id, project.code) == (unit.id, request.initial_project_code)
        role = session.scalar(select(Role).where(Role.code == "unit_admin"))
        binding = session.scalar(select(UnitMembershipRole))
        assert (binding.user_id, binding.unit_id, binding.role_id) == (user_id, unit.id, role.id)
        assert audit.action == "auth.identity.bound"
        assert audit.category == "security"
        assert audit.source == "auth"
        assert audit.status == "succeeded"
        assert audit.authorization_scope == "unit"
        assert audit.event_scope == "unit"
        assert audit.actor_roles_json == ["unit_admin"]
        persisted_audit = f"{audit.summary}{audit.metadata_json}"
        assert request.issuer not in persisted_audit
        assert request.subject not in persisted_audit


def test_identity_matching_does_not_normalize_or_merge(session_factory):
    requests = (
        make_request(1, issuer="https://id.example/issuer/", subject="Subject"),
        make_request(2, issuer="https://id.example/issuer", subject="Subject"),
        make_request(3, issuer="https://id.example/issuer/", subject="subject"),
        make_request(4, issuer="https://id.example/issuer/", subject=" Subject "),
    )
    with session_factory() as session:
        user_ids = [bootstrap_initial_unit_admin(session, request) for request in requests]
        identities = session.scalars(select(ExternalIdentity)).all()

        assert len(set(user_ids)) == 4
        assert {(item.issuer, item.subject) for item in identities} == {
            (request.issuer, request.subject) for request in requests
        }
        assert _count(session, User) == 4
        assert all(user.email is None for user in session.scalars(select(User)))


def test_second_active_unit_membership_for_same_user_is_rejected(session_factory):
    with session_factory() as session:
        user_id = bootstrap_initial_unit_admin(session, make_request())
        second_unit = Unit(code="unit-2", name="第二单位", status="active")
        session.add(second_unit)
        session.flush()
        session.add(UnitMembership(user_id=user_id, unit_id=second_unit.id, status="active"))

        with pytest.raises(ValueError, match="active unit membership"):
            session.commit()
        session.rollback()
        assert _count(session, UnitMembership) == 1


def test_active_unit_membership_can_move_atomically_while_locking_user(
    session_factory,
):
    with session_factory() as session:
        user_id = bootstrap_initial_unit_admin(session, make_request())
        second_unit = Unit(code="unit-2", name="第二单位", status="active")
        session.add(second_unit)
        session.flush()
        session.user_lock_calls = 0
        current_membership = session.scalar(
            select(UnitMembership).where(UnitMembership.user_id == user_id)
        )
        current_membership.status = "inactive"
        session.add(
            UnitMembership(
                user_id=user_id,
                unit_id=second_unit.id,
                status="active",
            )
        )

        session.commit()

        active_units = set(
            session.scalars(
                select(UnitMembership.unit_id).where(
                    UnitMembership.user_id == user_id,
                    UnitMembership.status == "active",
                )
            )
        )
        assert active_units == {second_unit.id}
        assert session.user_lock_calls >= 1


@pytest.mark.parametrize(
    "bootstrap_request",
    [
        replace(make_request(), issuer=""),
        replace(make_request(), subject=""),
        replace(make_request(), issuer="x" * 513),
        replace(make_request(), subject="x" * 256),
    ],
)
def test_invalid_identity_rolls_back_entire_bootstrap(
    session_factory, bootstrap_request
):
    with session_factory() as session:
        with pytest.raises(ValueError):
            bootstrap_initial_unit_admin(session, bootstrap_request)

        assert session.commit_calls == 0
        assert session.rollback_calls == 1
        assert _count(session, Unit) == 0
        assert _count(session, User) == 0
        assert _count(session, Permission) == 0
        assert _count(session, AuditEvent) == 0


def test_already_bound_subject_rolls_back_only_second_bootstrap(session_factory):
    with session_factory() as session:
        request = make_request()
        first_user_id = bootstrap_initial_unit_admin(session, request)
        with pytest.raises(ValueError, match="already bound"):
            bootstrap_initial_unit_admin(
                session,
                replace(
                    request,
                    unit_code="unit-2",
                    unit_name="第二单位",
                    initial_project_code="project-2",
                    initial_project_name="第二项目",
                ),
            )

        assert session.get(User, first_user_id) is not None
        assert _count(session, User) == 1
        assert _count(session, Unit) == 1
        assert _count(session, ExternalIdentity) == 1
        assert _count(session, AuditEvent) == 1


def test_audit_failure_rolls_back_catalogue_and_identity(session_factory):
    def reject_audit(mapper, connection, target):
        raise RuntimeError("audit unavailable")

    event.listen(AuditEvent, "before_insert", reject_audit)
    try:
        with session_factory() as session:
            with pytest.raises(RuntimeError, match="audit unavailable"):
                bootstrap_initial_unit_admin(session, make_request())

            assert session.commit_calls == 0
            assert session.rollback_calls == 1
            for model in (
                Unit, Project, User, ExternalIdentity, UnitMembership,
                ProjectMembership, Role, Permission, RolePermission,
                UnitMembershipRole, Menu, MenuPermission, AuditEvent,
            ):
                assert _count(session, model) == 0
    finally:
        event.remove(AuditEvent, "before_insert", reject_audit)


def test_database_error_is_rolled_back_and_sanitized_without_identity_values(
    session_factory,
):
    secret = "secret-issuer-subject-marker"

    def reject_identity(mapper, connection, target):
        raise IntegrityError(
            "INSERT external identity",
            {"issuer": secret, "subject": secret},
            Exception("simulated constraint failure"),
        )

    event.listen(ExternalIdentity, "before_insert", reject_identity)
    try:
        with session_factory() as session:
            request = make_request(issuer=secret, subject=secret)

            with pytest.raises(RuntimeError, match="^identity bootstrap failed$") as raised:
                bootstrap_initial_unit_admin(session, request)

            assert secret not in str(raised.value)
            assert raised.value.__cause__ is None
            assert raised.value.__suppress_context__ is True
            assert session.rollback_calls == 1
            assert _count(session, User) == 0
            assert _count(session, ExternalIdentity) == 0
    finally:
        event.remove(ExternalIdentity, "before_insert", reject_identity)


def test_cli_rejects_an_unprotected_json_request_file(tmp_path):
    request_file = tmp_path / "bootstrap.json"
    request_file.write_text(
        json.dumps(
            {
                "unit_code": "unit-1",
                "unit_name": "测试单位",
                "user_display_name": "初始管理员",
                "issuer": "https://id.example/issuer/",
                "subject": "Subject-1",
                "initial_project_code": "project-1",
                "initial_project_name": "初始项目",
            }
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(request_file), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
    else:
        request_file.chmod(0o644)

    with pytest.raises(ValueError, match="protected JSON"):
        _request_from_json(request_file)


@pytest.mark.parametrize(
    ("owner_sid", "expected"),
    [
        ("S-1-5-21-current", True),
        ("S-1-5-21-arbitrary-owner", False),
    ],
)
def test_windows_acl_requires_a_fixed_safe_owner(
    monkeypatch, owner_sid, expected
):
    completed = SimpleNamespace(
        stdout=json.dumps(
            {
                "current": "S-1-5-21-current",
                "owner": owner_sid,
                "allowed": ["S-1-5-21-current"],
            }
        )
    )
    monkeypatch.setattr(
        bootstrap_module.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )

    assert _windows_acl_is_protected(Path("bootstrap.json")) is expected


def test_protected_json_is_read_from_the_already_checked_descriptor(
    tmp_path, monkeypatch
):
    request_file = tmp_path / "bootstrap.json"
    request_file.write_text(
        json.dumps(
            {
                "unit_code": "unit-1",
                "unit_name": "测试单位",
                "user_display_name": "初始管理员",
                "issuer": "https://id.example/issuer/",
                "subject": "Subject-1",
                "initial_project_code": "project-1",
                "initial_project_name": "初始项目",
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        request_file.chmod(0o600)

    def forbid_reopen(*args, **kwargs):
        raise AssertionError("Path.read_text reopened the checked file")

    monkeypatch.setattr(Path, "read_text", forbid_reopen)

    request = _request_from_json(request_file)

    assert request.subject == "Subject-1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and symlink contract")
def test_posix_protected_json_rejects_a_symlink(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "bootstrap.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="protected JSON"):
        _request_from_json(link)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle identity contract")
def test_windows_rejects_path_replacement_after_open(tmp_path, monkeypatch):
    request_file = tmp_path / "bootstrap.json"
    request_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        bootstrap_module,
        "_windows_acl_is_protected",
        lambda path: True,
    )
    monkeypatch.setattr(
        bootstrap_module.os,
        "fstat",
        lambda descriptor: SimpleNamespace(
            st_dev=1,
            st_ino=1,
            st_mode=stat.S_IFREG | 0o600,
        ),
    )
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *args, **kwargs: SimpleNamespace(
            st_dev=2,
            st_ino=2,
            st_mode=stat.S_IFREG | 0o600,
        ),
    )

    with pytest.raises(ValueError, match="protected JSON"):
        _request_from_json(request_file)
