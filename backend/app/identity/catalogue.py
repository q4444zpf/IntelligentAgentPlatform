from dataclasses import dataclass

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.identity.models import (
    Menu,
    MenuPermission,
    Permission,
    Role,
    RolePermission,
    new_id,
)


PERMISSION_CODES = (
    "platform.read",
    "dashboard.read",
    "project.read",
    "project.manage",
    "project.member.manage",
    "identity.read",
    "identity.manage",
    "agent.read",
    "agent.manage",
    "agent.run",
    "conversation.read",
    "conversation.manage",
    "workflow.read",
    "workflow.manage",
    "workflow.run",
    "knowledge.read",
    "knowledge.manage",
    "knowledge.retrieve",
    "model.read",
    "model.manage",
    "model.run",
    "tool.read",
    "tool.manage",
    "tool.invoke",
    "mcp.read",
    "mcp.manage",
    "mcp.sync",
    "skill.read",
    "skill.manage",
    "skill.invoke",
    "collaboration.read",
    "collaboration.manage",
    "collaboration.run",
    "prompt.read",
    "prompt.manage",
    "resource.read",
    "resource.manage",
    "resource.publish",
    "resource.review",
    "artifact.read",
    "artifact.manage",
    "approval.read",
    "approval.manage",
    "policy.read",
    "policy.manage",
    "credential.read",
    "credential.manage",
    "settings.read",
    "settings.manage",
    "audit.read",
    "sandbox.read",
    "integration.read",
    "integration.manage",
)

ROLE_PERMISSION_CODES = {
    "project_admin": (
        "dashboard.read", "project.read", "project.manage",
        "project.member.manage", "agent.read", "agent.manage", "agent.run",
        "conversation.read", "conversation.manage", "workflow.read",
        "workflow.manage", "workflow.run", "knowledge.read",
        "knowledge.manage", "knowledge.retrieve", "model.read", "model.manage",
        "model.run", "tool.read", "tool.invoke", "mcp.read", "skill.read",
        "skill.invoke", "collaboration.read", "collaboration.manage",
        "collaboration.run", "prompt.read", "prompt.manage", "resource.read",
        "resource.manage", "resource.publish", "artifact.read",
        "artifact.manage", "approval.read", "integration.read",
    ),
    "business_operator": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run",
        "knowledge.read", "knowledge.retrieve", "model.read", "model.run",
        "tool.read", "tool.invoke", "skill.read", "skill.invoke",
        "collaboration.read", "collaboration.run", "resource.read",
        "artifact.read", "approval.read",
    ),
    "model_expert": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run",
        "knowledge.read", "knowledge.manage", "knowledge.retrieve",
        "model.read", "model.run", "tool.read", "tool.invoke", "skill.read",
        "skill.invoke", "prompt.read", "prompt.manage", "resource.read",
        "resource.manage", "artifact.read", "artifact.manage",
    ),
    "unit_auditor": (
        "platform.read", "dashboard.read", "project.read", "identity.read",
        "agent.read", "conversation.read", "workflow.read", "knowledge.read",
        "model.read", "tool.read", "mcp.read", "skill.read",
        "collaboration.read", "prompt.read", "resource.read", "artifact.read",
        "approval.read", "policy.read", "audit.read", "sandbox.read",
        "integration.read", "settings.read",
    ),
    "viewer": (
        "dashboard.read", "project.read", "agent.read", "conversation.read",
        "workflow.read", "knowledge.read", "model.read", "tool.read",
        "skill.read", "collaboration.read", "resource.read", "artifact.read",
    ),
}

_ROLE_DEFINITIONS = (
    ("unit_admin", "单位管理员", "unit"),
    ("project_admin", "项目管理员", "project"),
    ("business_operator", "业务操作员", "project"),
    ("model_expert", "模型专家", "project"),
    ("unit_auditor", "单位审计员", "unit"),
    ("viewer", "只读用户", "project"),
)


@dataclass(frozen=True)
class _MenuDefinition:
    key: str
    title: str
    permission_code: str
    visibility_target: str
    requires_current_project: bool


_GROUPS = (
    ("agents", "智能体"),
    ("capabilities", "能力"),
    ("resources", "资源"),
    ("operations", "运行"),
    ("security", "安全"),
    ("system", "系统"),
)

_ROOT_ROUTES = (
    _MenuDefinition("chat", "AI 对话", "agent.run", "unit", True),
    _MenuDefinition("dashboard", "工作台", "platform.read", "unit", False),
)

_GROUP_ROUTES = {
    "agents": (
        _MenuDefinition("agent-manage", "智能体管理", "agent.manage", "unit", False),
        _MenuDefinition(
            "collaboration", "多智能体协同", "collaboration.read",
            "current_project", True,
        ),
        _MenuDefinition("workflow", "流程编排", "workflow.read", "current_project", True),
    ),
    "capabilities": (
        _MenuDefinition("llm", "大模型管理", "model.manage", "unit", False),
        _MenuDefinition("mcp", "MCP 管理", "mcp.manage", "unit", False),
        _MenuDefinition("skill", "Skill 管理", "skill.manage", "unit", False),
        _MenuDefinition("tools", "工具注册中心", "tool.manage", "unit", False),
        _MenuDefinition("knowledge", "知识库管理", "knowledge.read", "current_project", True),
        _MenuDefinition("prompt", "Prompt 管理", "prompt.read", "current_project", True),
        _MenuDefinition(
            "external-agents", "外部智能体管理", "integration.read", "unit", False,
        ),
    ),
    "resources": (
        _MenuDefinition("my-agents", "我的智能体", "resource.read", "current_project", True),
        _MenuDefinition("my-mcp", "我的 MCP", "resource.read", "current_project", True),
        _MenuDefinition("my-skills", "我的 Skill", "resource.read", "current_project", True),
        _MenuDefinition(
            "my-publish", "我的发布申请", "resource.publish", "current_project", True,
        ),
        _MenuDefinition(
            "project-resources", "项目资源", "resource.read", "current_project", True,
        ),
        _MenuDefinition(
            "hydraulic-topology", "水利拓扑数据", "resource.read", "current_project", True,
        ),
        _MenuDefinition("unit-resources", "单位资源", "resource.read", "unit", False),
        _MenuDefinition(
            "public-agents", "公用智能体", "resource.read", "current_project", True,
        ),
        _MenuDefinition("public-mcp", "公用 MCP", "resource.read", "current_project", True),
        _MenuDefinition(
            "public-skills", "公用 Skill", "resource.read", "current_project", True,
        ),
        _MenuDefinition(
            "publish-review", "发布审核", "resource.review", "current_project", True,
        ),
    ),
    "operations": (
        _MenuDefinition("runs", "Agent Runs", "conversation.read", "current_project", True),
        _MenuDefinition("async-tasks", "异步任务", "workflow.read", "current_project", True),
        _MenuDefinition("sandbox", "沙箱监控", "sandbox.read", "unit", False),
        _MenuDefinition("artifacts", "成果文件", "artifact.read", "current_project", True),
    ),
    "security": (
        _MenuDefinition("approvals", "待办审批", "approval.read", "current_project", True),
        _MenuDefinition("policies", "风险策略", "policy.read", "unit", False),
        _MenuDefinition("credentials", "凭据管理", "credential.read", "unit", False),
        _MenuDefinition("audit", "审计日志", "audit.read", "unit", False),
    ),
    "system": (
        _MenuDefinition("users", "用户与权限", "identity.read", "unit", False),
        _MenuDefinition("unit-projects", "单位与项目", "project.read", "unit", False),
        _MenuDefinition("roles", "角色管理", "identity.read", "unit", False),
        _MenuDefinition("integration", "系统集成", "integration.read", "unit", False),
        _MenuDefinition("settings", "系统设置", "settings.read", "unit", False),
    ),
}


def _protect_builtin_codes(session: Session, flush_context, instances) -> None:
    for value in session.deleted:
        if isinstance(value, Permission) and value.code in PERMISSION_CODES:
            raise ValueError("built-in permission codes cannot be deleted")
        if isinstance(value, Role) and value.built_in:
            raise ValueError("built-in role codes cannot be deleted")

    for value in session.dirty:
        state = inspect(value)
        if isinstance(value, Permission):
            history = state.attrs.code.history
            if history.has_changes() and any(code in PERMISSION_CODES for code in history.deleted):
                raise ValueError("built-in permission codes cannot be renamed")
        elif isinstance(value, Role):
            was_builtin = value.built_in or True in state.attrs.built_in.history.deleted
            if was_builtin and (
                state.attrs.code.history.has_changes()
                or state.attrs.built_in.history.has_changes()
            ):
                raise ValueError("built-in role codes cannot be renamed")


event.listen(Session, "before_flush", _protect_builtin_codes)


def _seed_permissions(session: Session) -> None:
    existing = {
        permission.code: permission
        for permission in session.scalars(
            select(Permission).where(Permission.code.in_(PERMISSION_CODES))
        )
    }
    for code in PERMISSION_CODES:
        resource, action = code.split(".", 1)
        permission = existing.get(code)
        if permission is None:
            session.add(
                Permission(
                    id=new_id(),
                    code=code,
                    resource=resource,
                    action=action,
                    risk_level="medium",
                    status="active",
                )
            )
            continue
        if permission.resource != resource or permission.action != action:
            raise ValueError(f"permission catalogue conflict for {code}")
        permission.status = "active"


def _seed_roles(session: Session, unit_id: str) -> dict[str, Role]:
    existing = {
        role.code: role
        for role in session.scalars(select(Role).where(Role.unit_id == unit_id))
    }
    roles: dict[str, Role] = {}
    for code, name, scope_type in _ROLE_DEFINITIONS:
        role = existing.get(code)
        if role is None:
            role = Role(
                id=new_id(),
                unit_id=unit_id,
                code=code,
                name=name,
                scope_type=scope_type,
                built_in=True,
                status="active",
            )
            session.add(role)
        elif not role.built_in or role.scope_type != scope_type:
            raise ValueError(f"role catalogue conflict for {code}")
        else:
            role.name = name
            role.status = "active"
        roles[code] = role
    return roles


def _seed_grants(session: Session, unit_id: str, roles: dict[str, Role]) -> None:
    role_ids = tuple(role.id for role in roles.values())
    existing = {
        (grant.role_id, grant.permission_code, grant.data_scope)
        for grant in session.scalars(
            select(RolePermission).where(RolePermission.role_id.in_(role_ids))
        )
    }
    expected: list[tuple[Role, str, str]] = [
        (roles["unit_admin"], code, "unit") for code in PERMISSION_CODES
    ]
    for role_code, codes in ROLE_PERMISSION_CODES.items():
        scope = "unit" if role_code == "unit_auditor" else "project"
        expected.extend((roles[role_code], code, scope) for code in codes)
    expected.append((roles["business_operator"], "conversation.manage", "own"))

    for role, permission_code, data_scope in expected:
        key = (role.id, permission_code, data_scope)
        if key not in existing:
            session.add(
                RolePermission(
                    id=new_id(),
                    role_id=role.id,
                    permission_code=permission_code,
                    unit_id=unit_id,
                    data_scope=data_scope,
                )
            )


def _upsert_menu(
    session: Session,
    existing: dict[str, Menu],
    *,
    key: str,
    kind: str,
    title: str,
    sort_order: int,
    parent_id: str | None,
    route: _MenuDefinition | None = None,
) -> Menu:
    menu = existing.get(key)
    route_key = route.key if route is not None else None
    visibility_target = route.visibility_target if route is not None else None
    requires_project = route.requires_current_project if route is not None else False
    if menu is None:
        menu = Menu(
            id=new_id(),
            node_key=key,
            kind=kind,
            route_key=route_key,
            parent_id=parent_id,
            title=title,
            sort_order=sort_order,
            status="active",
            visibility_target=visibility_target,
            requires_current_project=requires_project,
        )
        session.add(menu)
        existing[key] = menu
        return menu
    if (
        menu.kind != kind
        or menu.route_key != route_key
        or menu.visibility_target != visibility_target
        or menu.requires_current_project != requires_project
    ):
        raise ValueError(f"menu catalogue conflict for {key}")
    menu.parent_id = parent_id
    menu.title = title
    menu.sort_order = sort_order
    menu.status = "active"
    return menu


def _seed_menus(session: Session) -> None:
    all_keys = {
        *(key for key, _ in _GROUPS),
        *(route.key for route in _ROOT_ROUTES),
        *(route.key for routes in _GROUP_ROUTES.values() for route in routes),
    }
    existing = {
        menu.node_key: menu
        for menu in session.scalars(select(Menu).where(Menu.node_key.in_(all_keys)))
    }
    for index, route in enumerate(_ROOT_ROUTES):
        _upsert_menu(
            session,
            existing,
            key=route.key,
            kind="route",
            title=route.title,
            sort_order=index,
            parent_id=None,
            route=route,
        )
    groups: dict[str, Menu] = {}
    for index, (key, title) in enumerate(_GROUPS, start=len(_ROOT_ROUTES)):
        groups[key] = _upsert_menu(
            session,
            existing,
            key=key,
            kind="group",
            title=title,
            sort_order=index,
            parent_id=None,
        )
    for parent_key, routes in _GROUP_ROUTES.items():
        for index, route in enumerate(routes):
            _upsert_menu(
                session,
                existing,
                key=route.key,
                kind="route",
                title=route.title,
                sort_order=index,
                parent_id=groups[parent_key].id,
                route=route,
            )

    session.flush()
    mappings = {
        (mapping.menu_id, mapping.permission_code)
        for mapping in session.scalars(
            select(MenuPermission).where(
                MenuPermission.menu_id.in_(tuple(menu.id for menu in existing.values()))
            )
        )
    }
    all_routes = (
        *_ROOT_ROUTES,
        *(route for routes in _GROUP_ROUTES.values() for route in routes),
    )
    for route in all_routes:
        key = (existing[route.key].id, route.permission_code)
        if key not in mappings:
            session.add(
                MenuPermission(
                    id=new_id(),
                    menu_id=key[0],
                    permission_code=key[1],
                )
            )


def seed_builtin_catalogue(session: Session, unit_id: str) -> None:
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id must be a non-empty string")
    _seed_permissions(session)
    roles = _seed_roles(session, unit_id)
    session.flush()
    _seed_grants(session, unit_id, roles)
    _seed_menus(session)
