# User Forms Required Unit Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require at least one active unit-scoped role whenever an administrator creates or edits an account, with an atomic backend contract and no role-removal bypass.

**Architecture:** Extend the create and update request contracts with required `role_ids`, then centralize active unit-role validation and binding replacement in `admin_router.py`. The Vue forms load active unit roles and send profile plus role IDs in one request; independent role mutation endpoints preserve the same last-unit-role invariant.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, pytest, Vue 3 Composition API, TypeScript, Ant Design Vue, Vitest, Vue Test Utils

## Global Constraints

- New and edit account forms require at least one unit role.
- Only roles with `scope_type === "unit"` and `status === "active"` are selectable.
- User profile fields and unit-role bindings are saved in one database transaction.
- Empty role lists, project roles, inactive roles, and roles outside the current unit return HTTP `422`.
- Unit-role mutations cannot remove a user's final unit role; project roles may still be fully removed.
- A changed role set increments `authorization_version` and revokes existing sessions; an unchanged role set does neither.
- Existing roleless accounts receive no automatic role grant.
- Preserve unrelated changes in the dirty worktree and stage only files named by each task.

---

## File Map

- `backend/app/identity/schemas.py`: required `role_ids` request fields.
- `backend/app/identity/admin_router.py`: role validation, atomic create/edit binding replacement, and last-role protection.
- `backend/tests/identity/test_admin_api.py`: API contract, transaction, session, and role-removal regression coverage.
- `frontend/src/api/identity.ts`: typed create/update payload contracts.
- `frontend/src/api/identity.test.ts`: serialized request contract coverage.
- `frontend/src/views/platform/UserManagementView.vue`: required role selectors and form loading/submission behavior.
- `frontend/src/views/platform/IdentityManagementViews.test.ts`: create/edit form behavior coverage.

### Task 1: Atomic Backend Create And Edit Contract

**Files:**
- Modify: `backend/app/identity/schemas.py:140-153`
- Modify: `backend/app/identity/admin_router.py:90-234`
- Modify: `backend/tests/identity/test_admin_api.py`

**Interfaces:**
- Consumes: `Role`, `UnitMembershipRole`, `revoke_user_sessions`, and the current request `unit_id`.
- Produces: `_active_unit_roles(session: Session, role_ids: list[str], unit_id: str) -> list[Role]` and `_replace_unit_role_bindings(session: Session, user_id: str, unit_id: str, roles: list[Role]) -> bool`.
- Produces: `CreateIdentityUserRequest.role_ids: list[str]` and `UpdateIdentityUserRequest.role_ids: list[str]`.

- [ ] **Step 1: Add test helpers and update existing successful/semantic create requests**

Add a fixture helper that derives a real role ID rather than copying catalogue internals:

```python
def role_id(code: str = "unit_admin", unit_id: str = "unit-1") -> str:
    with app.dependency_overrides[get_session]() as session:
        role = session.scalar(select(Role).where(Role.unit_id == unit_id, Role.code == code))
        assert role is not None
        return role.id
```

For every existing `/api/identity/users` test whose intended branch is not request-shape validation, add `"role_ids": [role_id()]` to its JSON body. Keep one new test intentionally missing `role_ids`.

- [ ] **Step 2: Write failing create-contract tests**

```python
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
```

Add this parameterized boundary test for project, inactive, and cross-unit roles:

```python
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
```

- [ ] **Step 3: Run create tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/identity/test_admin_api.py -q -k "create_user and (requires or atomically or rejects)"
```

Expected: failures because `role_ids` is not required and no `UnitMembershipRole` is created.

- [ ] **Step 4: Add request fields and shared role helpers**

In `schemas.py`:

```python
class CreateIdentityUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    project_id: str | None = None
    initial_password: str | None = Field(default=None, min_length=12, max_length=256)
    invite: bool | None = None
    role_ids: list[str] = Field(min_length=1, max_length=64)


class UpdateIdentityUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    role_ids: list[str] = Field(min_length=1, max_length=64)
```

In `admin_router.py`, add helpers before the routes:

```python
def _active_unit_roles(session: Session, role_ids: list[str], unit_id: str) -> list[Role]:
    unique_ids = list(dict.fromkeys(role_ids))
    roles = session.scalars(
        select(Role).where(
            Role.id.in_(unique_ids),
            Role.unit_id == unit_id,
            Role.scope_type == "unit",
            Role.status == "active",
        )
    ).all()
    by_id = {role.id: role for role in roles}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status_code=422, detail="请选择当前单位的有效单位角色")
    return [by_id[role_id] for role_id in unique_ids]


def _replace_unit_role_bindings(
    session: Session,
    user_id: str,
    unit_id: str,
    roles: list[Role],
) -> bool:
    existing = session.scalars(
        select(UnitMembershipRole).where(
            UnitMembershipRole.user_id == user_id,
            UnitMembershipRole.unit_id == unit_id,
        )
    ).all()
    existing_ids = {binding.role_id for binding in existing}
    desired_ids = {role.id for role in roles}
    for binding in existing:
        if binding.role_id not in desired_ids:
            session.delete(binding)
    for role in roles:
        if role.id not in existing_ids:
            session.add(UnitMembershipRole(
                id=new_id(), user_id=user_id, unit_id=unit_id,
                role_id=role.id, scope_type="unit",
            ))
    return existing_ids != desired_ids


def _record_user_change(
    session: Session,
    context: RequestContext,
    user: User,
    action: str,
    summary: str,
    now: datetime,
) -> None:
    AuditRecorder().record(session, AuditRecordRequest(
        unit_id=context.unit_id,
        project_id=None,
        user_id=context.user_id,
        actor_roles=context.role_codes,
        authorization_scope="unit",
        event_scope="unit",
        auth_method=None,
        category="management",
        source="system",
        action=action,
        status="succeeded",
        risk_level="high",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.display_name,
        summary=summary,
        metadata={"target_user_id": user.id},
        allowed_metadata_keys=frozenset({"target_user_id"}),
        idempotency_key=f"{action}:{user.id}:{now.isoformat()}",
        occurred_at=now,
    ))
```

- [ ] **Step 5: Make create and edit atomic**

In `create_user`, call `_active_unit_roles` before adding `User`, call `_replace_unit_role_bindings` after `session.flush()`, and keep the existing single `session.commit()`. Include unit-role summaries in the response:

```python
roles = _active_unit_roles(session, body.role_ids, context.unit_id)
# existing user, membership, project, and credential writes
_replace_unit_role_bindings(session, user.id, context.unit_id, roles)
_record_user_change(
    session, context, user, "identity.user.created",
    "Created an identity user", datetime.now(timezone.utc),
)
session.commit()
return AdminUser.from_row(
    user,
    "active",
    role_summaries=[
        AdminRoleSummary(role_id=role.id, code=role.code, name=role.name, scope_type="unit")
        for role in roles
    ],
    initial_password=body.initial_password,
    invitation_status=invitation_status,
)
```

In `update_user`, validate roles before mutating the user, call `_replace_unit_role_bindings`, and revoke only for a changed set:

```python
roles = _active_unit_roles(session, body.role_ids, context.unit_id)
user.display_name = _ensure_display_name_available(...)
user.email = _ensure_email_available(...)
roles_changed = _replace_unit_role_bindings(session, user.id, context.unit_id, roles)
if roles_changed:
    _bump(user)
    revoke_user_sessions(session, user.id, "role_changed")
_record_user_change(
    session, context, user, "identity.user.updated",
    "Updated an identity user", datetime.now(timezone.utc),
)
session.commit()
```

The audit record is added before the same commit as profile and role writes. Extend the create and edit tests with:

```python
with app.dependency_overrides[get_session]() as session:
    assert session.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.resource_id == user_id,
        AuditEvent.action == "identity.user.created",
    )) == 1
```

For the edit test, use `resource_id == "user-1"` and `action == "identity.user.updated"`.

- [ ] **Step 6: Write failing edit/session tests, then make them pass**

Add two tests using an `AuthSession` record:

```python
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
```

Run each test before implementation and confirm its assertion fails for the intended missing behavior. Then run:

```powershell
python -m pytest tests/identity/test_admin_api.py -q
```

Expected: all identity admin API tests pass.

- [ ] **Step 7: Commit Task 1**

```powershell
git add backend/app/identity/schemas.py backend/app/identity/admin_router.py backend/tests/identity/test_admin_api.py
git commit -m "feat: require unit roles when saving users"
```

### Task 2: Protect The Final Unit Role Across Role APIs

**Files:**
- Modify: `backend/app/identity/admin_router.py:427-499,643-662`
- Modify: `backend/tests/identity/test_admin_api.py`

**Interfaces:**
- Consumes: `_replace_unit_role_bindings` from Task 1.
- Produces: `_users_with_only_unit_role(session: Session, role_id: str, unit_id: str) -> set[str]` for destructive-role validation.

- [ ] **Step 1: Write failing last-role tests**

Add literal behavior tests:

```python
def test_remove_role_rejects_the_users_last_unit_role():
    client = build_client()
    response = client.request(
        "DELETE", "/api/identity/users/user-1/roles",
        headers=headers(), json={"role_id": role_id()},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "用户必须至少保留一个单位角色"


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
    assert project_response.status_code == 200
```

Add a custom unit role as one user's only unit role and prove role deletion cannot bypass the invariant:

```python
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
        session.add_all([user, role])
        session.add(UnitMembership(
            id="last-role-membership", user_id=user.id, unit_id="unit-1", status="active",
        ))
        session.add(UnitMembershipRole(
            id="last-role-binding", user_id=user.id, unit_id="unit-1",
            role_id=role.id, scope_type="unit",
        ))
        session.commit()

    response = client.delete("/api/identity/roles/last-custom-role", headers=headers())

    assert response.status_code == 409
    with app.dependency_overrides[get_session]() as session:
        assert session.get(Role, "last-custom-role") is not None
        assert session.get(UnitMembershipRole, "last-role-binding") is not None
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
cd backend
python -m pytest tests/identity/test_admin_api.py -q -k "last_unit_role or empty_list"
```

Expected: current endpoints return `200` and delete the final binding.

- [ ] **Step 3: Implement last-role guards**

Before deleting a unit binding in `remove_role`, count the user's unit bindings and reject when the target exists and count is one:

```python
remaining_count = session.scalar(
    select(func.count(UnitMembershipRole.id)).where(
        UnitMembershipRole.user_id == user_id,
        UnitMembershipRole.unit_id == context.unit_id,
    )
) or 0
if binding is not None and remaining_count <= 1:
    raise HTTPException(status_code=422, detail="用户必须至少保留一个单位角色")
```

In `replace_roles`, reject `project is None and not role_ids`, and use `_active_unit_roles` plus `_replace_unit_role_bindings` for the unit branch. Keep the project branch's existing empty-list behavior.

Before deleting a custom unit role, identify bindings whose users have no other unit-role binding:

```python
def _users_with_only_unit_role(
    session: Session,
    role_id: str,
    unit_id: str,
) -> set[str]:
    role_counts = (
        select(
            UnitMembershipRole.user_id.label("user_id"),
            func.count(UnitMembershipRole.id).label("role_count"),
        )
        .where(UnitMembershipRole.unit_id == unit_id)
        .group_by(UnitMembershipRole.user_id)
        .subquery()
    )
    return set(session.scalars(
        select(UnitMembershipRole.user_id)
        .join(role_counts, role_counts.c.user_id == UnitMembershipRole.user_id)
        .where(
            UnitMembershipRole.unit_id == unit_id,
            UnitMembershipRole.role_id == role_id,
            role_counts.c.role_count == 1,
        )
    ))


if role.scope_type == "unit" and _users_with_only_unit_role(session, role.id, context.unit_id):
    raise HTTPException(status_code=409, detail="该角色是用户的最后一个单位角色，不能删除")
```

- [ ] **Step 4: Run the complete backend identity suite**

```powershell
cd backend
python -m pytest tests/identity -q
```

Expected: all tests pass, including project-role removal tests.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/app/identity/admin_router.py backend/tests/identity/test_admin_api.py
git commit -m "fix: preserve users final unit role"
```

### Task 3: Typed Frontend API Contract

**Files:**
- Modify: `frontend/src/api/identity.ts:18-29,72-77`
- Modify: `frontend/src/api/identity.test.ts`

**Interfaces:**
- Produces: `CreateIdentityUserPayload.role_ids: string[]`.
- Produces: `UpdateIdentityUserPayload` with `display_name`, optional `email`, and required `role_ids`.

- [ ] **Step 1: Write failing request serialization tests**

Update the create test and add an update test that assert literal request bodies:

```typescript
await createIdentityUser({
  display_name: 'Alice',
  email: 'alice@example.test',
  initial_password: 'InitialPassword123!',
  role_ids: ['role-1'],
});
expect(fetchMock).toHaveBeenCalledWith(
  '/api/identity/users',
  expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({
      display_name: 'Alice',
      email: 'alice@example.test',
      initial_password: 'InitialPassword123!',
      role_ids: ['role-1'],
    }),
  }),
);
```

The update test must expect `PATCH /api/identity/users/user-1` with `role_ids: ['role-2']` in the same body as the profile fields.

- [ ] **Step 2: Run type checking and verify RED**

```powershell
cd frontend
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vue-tsc/bin/vue-tsc.js --noEmit
```

Expected: FAIL with `TS2353` because the update payload type has no `role_ids` property.

- [ ] **Step 3: Add required payload types**

```typescript
export interface CreateIdentityUserPayload {
  display_name: string;
  email?: string | null;
  project_id?: string | null;
  initial_password?: string | null;
  invite?: boolean | null;
  role_ids: string[];
}

export interface UpdateIdentityUserPayload {
  display_name: string;
  email?: string | null;
  role_ids: string[];
}
```

Change `updateIdentityUser` to accept `UpdateIdentityUserPayload` without changing its URL or HTTP method.

- [ ] **Step 4: Run the API tests and commit**

```powershell
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vitest/vitest.mjs run src/api/identity.test.ts --pool=threads --maxWorkers=1
& $nodeRuntime node_modules/vue-tsc/bin/vue-tsc.js --noEmit
git add frontend/src/api/identity.ts frontend/src/api/identity.test.ts
git commit -m "feat: include unit roles in user payloads"
```

Expected: API tests pass.

### Task 4: Required Unit Role Selectors In Create And Edit Forms

**Files:**
- Modify: `frontend/src/views/platform/UserManagementView.vue:64-80,128-312`
- Modify: `frontend/src/views/platform/IdentityManagementViews.test.ts`

**Interfaces:**
- Consumes: `listIdentityRoles`, `listIdentityUserRoles`, typed create/update payloads from Task 3.
- Produces: create/edit forms whose `role_ids: string[]` is always non-empty at submission.

- [ ] **Step 1: Make the Ant Select test stub represent multiple selection correctly**

Import `defineComponent` from Vue and replace the select stub with a typed multiple-select representation:

```typescript
const SelectStub = defineComponent({
  props: {
    value: { type: [String, Array], default: '' },
    options: { type: Array, default: () => [] },
    mode: { type: String, default: '' },
  },
  emits: ['update:value', 'change'],
  setup(props, { emit }) {
    function onChange(event: Event) {
      const target = event.target as HTMLSelectElement;
      const value = props.mode === 'multiple'
        ? Array.from(target.selectedOptions).map((option) => option.value)
        : target.value;
      emit('update:value', value);
      emit('change', value);
    }
    return { onChange };
  },
  template: `<select
    :multiple="mode === 'multiple'"
    :value="value"
    @change="onChange"
  ><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>`,
});

const stubs = {
  'a-select': SelectStub,
};
```

Keep every other existing stub entry unchanged when replacing only the `a-select` value.

- [ ] **Step 2: Write failing create-form tests**

Use one active unit role plus project/inactive roles in `mocks.listRoles`. Create `const messageError = vi.spyOn(message, 'error').mockImplementation(() => undefined as never)`. Open the create modal and assert only the active unit role appears. Fill name, email, and password, then save without a role:

```typescript
expect(mocks.createUser).not.toHaveBeenCalled();
expect(messageError).toHaveBeenCalledWith('请至少选择一个单位角色');
```

Select `role-1`, save again, and expect one atomic request:

```typescript
expect(mocks.createUser).toHaveBeenCalledWith({
  display_name: 'Alice',
  email: 'alice@example.test',
  initial_password: 'InitialPassword123!',
  role_ids: ['role-1'],
});
expect(mocks.replaceUserRoles).not.toHaveBeenCalled();
```

- [ ] **Step 3: Run create-form tests and verify RED**

```powershell
cd frontend
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vitest/vitest.mjs run src/views/platform/IdentityManagementViews.test.ts --pool=threads --maxWorkers=1 -t "unit role"
```

Expected: no role selector exists and the create payload lacks `role_ids`.

- [ ] **Step 4: Implement shared active unit-role loading and create form behavior**

Add `role_ids` to the create form state and a dedicated loading flag:

```typescript
let accountRoleController: AbortController | null = null;
const accountRolesLoading = ref(false);
const createForm = ref({ display_name: '', email: '', initial_password: '', role_ids: [] as string[] });
const editForm = ref({ display_name: '', email: '', role_ids: [] as string[] });
const unitRoleOptions = computed(() => availableRoles.value
  .filter((role) => role.status === 'active' && role.scope_type === 'unit')
  .map((role) => ({ label: `${role.name} (${role.code})`, value: role.id })));
```

Implement create-role loading with the dedicated controller. `openCreate` opens the modal, resets `role_ids`, and calls this function:

```typescript
async function loadCreateUnitRoles(): Promise<void> {
  accountRoleController?.abort();
  accountRoleController = new AbortController();
  accountRolesLoading.value = true;
  try {
    availableRoles.value = await listIdentityRoles(accountRoleController.signal);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    errorMessage.value = error instanceof ApiError ? error.message : '单位角色加载失败';
  } finally {
    accountRolesLoading.value = false;
  }
}

function openCreate(): void {
  createForm.value = {
    display_name: '', email: '', initial_password: randomPassword(), role_ids: [],
  };
  createOpen.value = true;
  void loadCreateUnitRoles();
}
```

Add this form item:

```vue
<a-form-item label="单位角色" required>
  <a-select
    v-model:value="createForm.role_ids"
    mode="multiple"
    :loading="accountRolesLoading"
    :options="unitRoleOptions"
    placeholder="请选择至少一个单位角色"
  />
</a-form-item>
```

Before setting `creating`, reject an empty array with `message.error('请至少选择一个单位角色')`. Include `role_ids` in `createIdentityUser` and reset it after success.

- [ ] **Step 5: Write failing edit-form tests**

Open Edit for a user and make `mocks.listUserRoles` return both one unit role and one project role. Assert `listUserRoles` was called with `('user-1', null, expect.any(AbortSignal))` and the form preselects only the unit role. Clear the multiple select, click save, and assert `updateUser` is not called. Re-select a unit role and expect:

```typescript
expect(mocks.updateUser).toHaveBeenCalledWith('user-1', {
  display_name: 'Alice Updated',
  email: 'alice@example.test',
  role_ids: ['role-2'],
});
```

Also cover a legacy roleless user: Edit opens with an empty selection, but Save remains blocked until an active unit role is selected.

- [ ] **Step 6: Run edit-form tests and verify RED**

```powershell
cd frontend
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vitest/vitest.mjs run src/views/platform/IdentityManagementViews.test.ts --pool=threads --maxWorkers=1 -t "edit.*unit role"
```

Expected: the edit modal has no role selector and update payload lacks `role_ids`.

- [ ] **Step 7: Implement edit form behavior**

Make `openEdit` load the current bindings and role directory concurrently. Use a dedicated `accountRoleController` so closing or reopening an account form does not abort the independent role-management dialog:

```typescript
async function openEdit(user: IdentityUser): Promise<void> {
  editUserId.value = user.id;
  editForm.value = {
    display_name: user.display_name,
    email: user.email || '',
    role_ids: [],
  };
  editOpen.value = true;
  accountRoleController?.abort();
  accountRoleController = new AbortController();
  accountRolesLoading.value = true;
  try {
    const [roles, current] = await Promise.all([
      listIdentityRoles(accountRoleController.signal),
      listIdentityUserRoles(user.id, null, accountRoleController.signal),
    ]);
    availableRoles.value = roles;
    editForm.value.role_ids = current
      .filter((role) => role.scope_type === 'unit')
      .map((role) => role.role_id);
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '单位角色加载失败';
  } finally {
    accountRolesLoading.value = false;
  }
}
```

Add the same required selector to the edit modal. Reject empty `editForm.role_ids`, and submit profile plus role IDs through `updateIdentityUser` only. Abort `accountRoleController` in `onBeforeUnmount`.

- [ ] **Step 8: Run focused frontend tests and commit**

```powershell
cd frontend
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vitest/vitest.mjs run src/views/platform/IdentityManagementViews.test.ts src/api/identity.test.ts --pool=threads --maxWorkers=1
git add frontend/src/views/platform/UserManagementView.vue frontend/src/views/platform/IdentityManagementViews.test.ts
git commit -m "feat: require unit roles in user forms"
```

Expected: both files pass with no console errors or unhandled promises.

### Task 5: Full Regression And Deployment Acceptance

**Files:**
- Verify only; do not stage generated `frontend/dist` or temporary deployment archives.

**Interfaces:**
- Consumes: completed backend and frontend behavior from Tasks 1-4.
- Produces: verified local build and a server release whose Web and API containers use the same source contract.

- [ ] **Step 1: Run complete backend regression**

```powershell
cd backend
python -m pytest -q
```

Expected: zero failed tests. Investigate any failure rather than excluding it.

- [ ] **Step 2: Run complete frontend regression and production build**

```powershell
cd frontend
$nodeRuntime = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $nodeRuntime node_modules/vitest/vitest.mjs run --pool=threads --maxWorkers=1
& $nodeRuntime node_modules/vue-tsc/bin/vue-tsc.js --noEmit
& $nodeRuntime node_modules/vite/bin/vite.js build
```

Expected: zero failed tests, type-check exit `0`, build exit `0`.

- [ ] **Step 3: Inspect the final diff and commit any test-only follow-up**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Stage only feature files from the File Map. Do not include existing unrelated changes in `AGENTS.md`, `compose.yaml`, backend runtime tests, temporary directories, or document artifacts.

- [ ] **Step 4: Deploy API and Web together**

Create a timestamped release under `/home/<deploy-user>/intelligent-agent-platform/releases`, copy the current release, overlay the committed feature files, then run:

```bash
docker compose -f compose.yaml -f compose.http-staging.yaml build api web
docker compose -f compose.yaml -f compose.http-staging.yaml up -d api web
docker compose -f compose.yaml -f compose.http-staging.yaml ps api web postgres
curl -fsS http://127.0.0.1:<http-port>/api/health
```

Only after health succeeds, atomically repoint `/home/<deploy-user>/intelligent-agent-platform/current` to the new release. Do not run `docker compose down -v` and do not modify PostgreSQL volumes.

- [ ] **Step 5: Browser acceptance**

Using an administrator session:

1. Open “用户与权限” and “新建用户”.
2. Verify only active unit roles appear and Save is blocked with no role.
3. Create a disposable acceptance user with one unit role and verify the user list shows that role.
4. Open Edit, clear roles, and verify Save is blocked.
5. Change to another valid unit role, save, and verify the old session is revoked if one exists.
6. Delete the disposable acceptance user after explicit user confirmation because deletion is destructive.

Monitor Web/API logs during the flow. Expected: no HTTP `500`, no infinite navigation, and no account committed without a unit-role binding.
