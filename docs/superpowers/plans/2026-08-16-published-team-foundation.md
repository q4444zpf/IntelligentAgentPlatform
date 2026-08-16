# Published Multi-Agent Team Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build project-scoped, versioned multi-agent teams that can be managed, published, selected in chat, and executed through the existing isolated Runner Gateway path.

**Architecture:** Add a focused `collaboration` backend module with PostgreSQL Team aggregates, immutable published versions, and embedded Agent definition snapshots. Resolve a published Team version when accepting a conversation Run, extend the execution snapshot with a discriminated Team actor, and compile a bounded platform-owned supervisor graph in the existing Sandbox Runtime. Replace frontend placeholders with a real Team management view and API-backed Team selection.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, PostgreSQL, LangGraph/DeepAgents, Pytest, Vue 3 Composition API, TypeScript, Pinia, Ant Design Vue, Vitest.

## Global Constraints

- A Team has exactly one supervisor and at least one distinct member; nested Teams are forbidden.
- Published Team versions are immutable, and an accepted Run always uses the version selected at message acceptance.
- Current Agent storage has no independent publication/version table. Team publication must embed each enabled Agent's complete executable definition and SHA-256 digest; that digest is the concrete Agent version identifier within the Team version.
- Effective member capability authorization is the intersection of initiating-user access, Team whitelist, Agent bindings, member whitelist, and current capability availability/policy.
- All privileged model, Tool, MCP, knowledge, checkpoint, event, approval, and Artifact operations remain behind Runner Gateway and existing platform services.
- Serial scheduling is the default; parallel work is limited by the published `max_parallel_members`, Runner `max_subagents`, platform deadline, and step ceilings.
- This phase excludes visual workflow design, shared Team conversations, dynamic membership, nested Teams, arbitrary Python nodes, direct device control, and automatic replay of uncertain side effects.
- Every behavior change starts with a failing focused test and ends with an independent conventional commit containing only feature files.
- Preserve unrelated changes already present in the main workspace.

---

## File Structure

### Backend collaboration module

- Create `backend/app/collaboration/models.py`: SQLAlchemy Team, TeamVersion, and TeamVersionMember records and database constraints.
- Create `backend/app/collaboration/schemas.py`: request, response, draft, member, lifecycle, and stable error payload models.
- Create `backend/app/collaboration/repository.py`: project-scoped persistence, optimistic draft updates, version reads, and atomic publish storage.
- Create `backend/app/collaboration/service.py`: validation, Agent snapshot capture, canonical digest generation, permissions, lifecycle operations, and audit calls.
- Create `backend/app/collaboration/router.py`: `/api/collaboration/teams` routes and error/status mapping.
- Create `backend/app/collaboration/__init__.py`: package marker only.
- Modify `backend/app/db/base.py`: import collaboration models for metadata registration.
- Modify `backend/app/main.py`: mount the collaboration router.
- Create `backend/alembic/versions/20260816_21_published_teams.py`: PostgreSQL schema, indexes, uniqueness, foreign keys, and downgrade.

### Conversation and execution

- Modify `backend/app/conversations/models.py`: persist `actor_version_id` on AgentRun.
- Modify `backend/app/conversations/schemas.py`: expose the optional actor version.
- Modify `backend/app/conversations/service.py`: resolve Team actors transactionally and audit `resource_type="team"`.
- Modify `backend/app/conversations/router.py`: inject TeamService and map `team_unavailable`.
- Modify `backend/app/runtime/execution_snapshot.py`: discriminated Agent/Team actor snapshots and Team version resolution.
- Create `backend/app/runtime/team_graph.py`: plan schema validation, bounded scheduling, deterministic join, and supervisor synthesis graph construction.
- Modify `backend/app/runtime/sandbox_runtime.py`: select Agent or Team runtime by actor discriminator.
- Modify `backend/app/runtime/harness.py`: remove the obsolete unconditional Team rejection after snapshot support is active.

### Frontend

- Create `frontend/src/api/teams.ts`: typed Team CRUD, draft, publish, toggle, and version APIs.
- Create `frontend/src/views/collaboration/TeamManageView.vue`: searchable directory, editor, publication, version history, and permission states.
- Create `frontend/src/views/collaboration/TeamManageView.test.ts`: management behavior and responsive state tests.
- Modify `frontend/src/router/routes.ts`: route `/collaboration` to TeamManageView.
- Modify `frontend/src/views/agent/AgentConsoleView.vue`: enable Team mode and load real Teams/members.
- Modify `frontend/src/views/agent/AgentConsoleView.test.ts`: Team selection, payload, empty state, events, and failure behavior.
- Modify `frontend/src/api/runEvents.ts` and `frontend/src/features/chat/runtimeStatus.ts`: typed Team event labels and member/task metadata.

### Tests

- Create `backend/tests/collaboration/test_models.py`.
- Create `backend/tests/collaboration/test_repository.py`.
- Create `backend/tests/collaboration/test_service.py`.
- Create `backend/tests/collaboration/test_api.py`.
- Create `backend/tests/runtime/test_team_graph.py`.
- Modify `backend/tests/runtime/test_execution_snapshot.py`.
- Modify `backend/tests/runtime/test_sandbox_runtime.py`.
- Modify `backend/tests/runtime/test_harness.py`.
- Modify `backend/tests/conversations/test_service.py` and `backend/tests/conversations/test_api.py`.
- Modify `backend/tests/integration/test_postgres_migrations.py`.
- Create `tests/e2e/test_published_team_run.py`: backend-facing end-to-end acceptance using the real persistence and Runner gateway seams.

---

### Task 1: Team Persistence And Migration

**Files:**
- Create: `backend/app/collaboration/__init__.py`
- Create: `backend/app/collaboration/models.py`
- Create: `backend/app/collaboration/repository.py`
- Create: `backend/alembic/versions/20260816_21_published_teams.py`
- Modify: `backend/app/db/base.py`
- Test: `backend/tests/collaboration/test_models.py`
- Test: `backend/tests/collaboration/test_repository.py`
- Test: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**
- Produces: `Team`, `TeamVersion`, `TeamVersionMember`, and `TeamRepository`.
- Produces: `TeamRepository.get_scoped(unit_id, project_id, team_id)`, `list_scoped(...)`, `save_draft(...)`, `publish(...)`, and `get_version(...)`.
- Consumes: existing SQLAlchemy `Base`, session factories, identity unit/project tables, and Agent string IDs.

- [ ] **Step 1: Write failing model and repository tests**

```python
def test_team_requires_one_supervisor_and_published_versions_are_immutable(session):
    repository = TeamRepository(session)
    team = repository.create(unit_id="u1", project_id="p1", name="联合研判", created_by="user-1")
    repository.save_draft(team.id, expected_revision=1, definition=valid_definition())
    published = repository.publish(team.id, expected_revision=2, definition_digest="a" * 64, published_by="user-1")
    assert published.version == 1
    assert published.status == "published"
    with pytest.raises(TeamVersionImmutableError):
        repository.replace_published_definition(published.id, valid_definition())


def test_repository_hides_cross_project_team(session):
    team = TeamRepository(session).create(unit_id="u1", project_id="p1", name="A", created_by="x")
    assert TeamRepository(session).get_scoped("u1", "p2", team.id) is None
```

- [ ] **Step 2: Run focused tests and confirm missing module/schema failures**

Run: `cd backend && pytest tests/collaboration/test_models.py tests/collaboration/test_repository.py -q`

Expected: FAIL because `app.collaboration` and its tables do not exist.

- [ ] **Step 3: Implement models and repository boundaries**

```python
class Team(Base):
    __tablename__ = "collaboration_teams"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    published_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class TeamRepository:
    def get_scoped(self, unit_id: str, project_id: str, team_id: str) -> Team | None:
        return self.session.scalar(select(Team).where(
            Team.id == team_id, Team.unit_id == unit_id, Team.project_id == project_id
        ))
```

Use normalized version/member tables, a unique `(project_id, name)` constraint, a unique `(team_id, version)` constraint, one draft per Team, deterministic member positions, and database checks for positive limits and valid roles.

- [ ] **Step 4: Add and test Alembic migration**

Extend `test_postgres_migrations.py` to upgrade through revision `20260816_21`, assert all three tables, constraints and indexes exist, then downgrade one revision and assert removal.

Run: `cd backend && pytest tests/integration/test_postgres_migrations.py -q`

Expected: PASS against configured PostgreSQL; SKIP only when the repository's existing PostgreSQL fixture is unavailable.

- [ ] **Step 5: Run persistence tests**

Run: `cd backend && pytest tests/collaboration/test_models.py tests/collaboration/test_repository.py tests/integration/test_postgres_migrations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/collaboration backend/app/db/base.py backend/alembic/versions/20260816_21_published_teams.py backend/tests/collaboration/test_models.py backend/tests/collaboration/test_repository.py backend/tests/integration/test_postgres_migrations.py
git commit -m "feat: add published team persistence"
```

### Task 2: Team Lifecycle Service And Public API

**Files:**
- Create: `backend/app/collaboration/schemas.py`
- Create: `backend/app/collaboration/service.py`
- Create: `backend/app/collaboration/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/collaboration/test_service.py`
- Test: `backend/tests/collaboration/test_api.py`

**Interfaces:**
- Consumes: `TeamRepository`, `AgentService.get(agent_id)`, Tool registry resolution, `RequestContext`, and `AuditRecorder`.
- Produces: `TeamService.create`, `list`, `get`, `update_metadata`, `save_draft`, `publish`, `set_enabled`, `get_published_version`, and `resolve_for_run`.
- Produces: `ResolvedTeamRunActor(team_id: str, version_id: str, definition_digest: str)`.

- [ ] **Step 1: Write failing lifecycle and authorization tests**

```python
def test_publish_captures_immutable_agent_definitions_and_capability_intersection(service, agent_service):
    team = service.create(context("u1", "p1", permissions={"collaboration.manage"}), TeamCreate(name="联合研判"))
    service.save_draft(context(...), team.id, TeamDraftUpdate(
        revision=team.draft_revision,
        supervisor=member("supervisor", tools=["forecast.read"]),
        members=[member("reviewer", tools=["forecast.read"])],
        tool_ids=["forecast.read"], max_steps=6, max_parallel_members=2,
    ))
    published = service.publish(context(...), team.id)
    assert published.members[0].agent_definition_digest
    agent_service.update("reviewer", changed_prompt())
    assert service.get_version(context(...), team.id, 1).members[0].system_prompt != changed_prompt().system_prompt


def test_collaboration_run_permission_is_required(service):
    with pytest.raises(TeamPermissionError):
        service.resolve_for_run(context(permissions={"collaboration.read"}), "team-1")
```

- [ ] **Step 2: Run tests and confirm service/API failures**

Run: `cd backend && pytest tests/collaboration/test_service.py tests/collaboration/test_api.py -q`

Expected: FAIL because schemas, service, routes, and error mapping are absent.

- [ ] **Step 3: Implement schemas, canonical Agent snapshots, and validation**

```python
class TeamMemberDraft(BaseModel):
    agent_id: str
    role: Literal["supervisor", "member"]
    responsibility: str = Field(min_length=1, max_length=500)
    tool_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    knowledge_source_ids: list[str] = Field(default_factory=list)


class ResolvedTeamRunActor(BaseModel):
    team_id: str
    version_id: str
    definition_digest: str
```

Canonicalize embedded Agent definitions with sorted-key UTF-8 JSON and store SHA-256 digests. Validate unique membership, enabled Agents, whitelist subsets, positive bounded limits, and current project scope. Map `collaboration.read/manage/run` from `RequestContext.permissions`; never trust client unit/project fields.

- [ ] **Step 4: Implement API routes and stable errors**

Mount `/api/collaboration/teams` and implement list, create, detail, patch metadata, draft update, publish, enable, disable, version list, and version detail. Return `404 team_not_found`, `409 team_draft_conflict`, `422 team_definition_invalid`, and `422 team_resource_unavailable` as structured details.

- [ ] **Step 5: Verify lifecycle, permissions, audit, and API contract**

Run: `cd backend && pytest tests/collaboration/test_service.py tests/collaboration/test_api.py tests/audit/test_api.py tests/identity/test_authorization.py -q`

Expected: PASS, including audit records with `resource_type="team"` and cross-project `404` behavior.

- [ ] **Step 6: Commit**

```bash
git add backend/app/collaboration/schemas.py backend/app/collaboration/service.py backend/app/collaboration/router.py backend/app/main.py backend/tests/collaboration/test_service.py backend/tests/collaboration/test_api.py
git commit -m "feat: add published team lifecycle api"
```

### Task 3: Transactional Team Run Resolution And Snapshot Versioning

**Files:**
- Modify: `backend/app/conversations/models.py`
- Modify: `backend/app/conversations/schemas.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/conversations/router.py`
- Modify: `backend/app/runtime/execution_snapshot.py`
- Modify: `backend/alembic/versions/20260816_21_published_teams.py`
- Test: `backend/tests/conversations/test_service.py`
- Test: `backend/tests/conversations/test_api.py`
- Test: `backend/tests/runtime/test_execution_snapshot.py`

**Interfaces:**
- Consumes: `TeamService.resolve_for_run(context, team_id) -> ResolvedTeamRunActor` and `TeamService.get_version_by_id(version_id)`.
- Produces: `AgentRun.actor_version_id: str | None` and Team-shaped `ExecutionSnapshotPayload.actor`.
- Preserves: existing Agent actor snapshot compatibility and single-Agent message behavior.

- [ ] **Step 1: Write failing transactional resolution tests**

```python
def test_team_message_persists_selected_version_and_team_audit(service, repository, team_service):
    accepted = service.create_message(context_with("collaboration.run"), "conversation-1", MessageCreate(
        content="联合研判", actor_type="team", actor_id="team-1"
    ))
    assert accepted.run.actor_version_id == "team-version-1"
    assert repository.get_audit(accepted.run.id).resource_type == "team"


def test_unavailable_team_does_not_persist_message_or_run(service, repository):
    with pytest.raises(AgentSelectionError, match="team_unavailable"):
        service.create_message(context_with("collaboration.run"), "conversation-1", MessageCreate(
            content="x", actor_type="team", actor_id="disabled-team"
        ))
    assert repository.count_messages("conversation-1") == 0
    assert repository.count_runs("conversation-1") == 0
```

- [ ] **Step 2: Run tests and confirm current placeholder resolution fails**

Run: `cd backend && pytest tests/conversations/test_service.py tests/conversations/test_api.py -q`

Expected: FAIL because Team IDs are currently returned without resource validation or a version ID.

- [ ] **Step 3: Persist actor version and resolve within the message transaction**

Add nullable `actor_version_id` to `agent_runs` in the same migration. Change `_resolve_actor` to return an actor resolution object rather than a bare string. Roll back the session on Team resolution or audit failure before message commit.

```python
@dataclass(frozen=True)
class ActorResolution:
    actor_id: str
    actor_version_id: str | None
    resource_type: Literal["agent", "team"]
```

- [ ] **Step 4: Write failing Team snapshot tests**

Assert schema version `4`, discriminator `actor.kind == "team"`, exact selected Team version, embedded supervisor/member digests, ordered members, limits, and unchanged payload after a later publish.

Run: `cd backend && pytest tests/runtime/test_execution_snapshot.py -q`

Expected: FAIL because only `PublishedAgentSnapshot` is supported.

- [ ] **Step 5: Implement discriminated snapshot actors**

```python
class PublishedTeamSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["team"] = "team"
    id: str
    version_id: str
    version: int
    definition_digest: str
    supervisor: SnapshotTeamMember
    members: tuple[SnapshotTeamMember, ...]
    max_steps: int
    max_parallel_members: int
    failure_strategy: Literal["fail_fast", "continue_then_synthesize"]
```

Make Agent actor snapshots carry `kind: Literal["agent"]`. Maintain canonical read support for schema versions 1-3 and write Team-capable snapshots as schema version 4.

- [ ] **Step 6: Run conversation and snapshot regressions**

Run: `cd backend && pytest tests/conversations tests/runtime/test_execution_snapshot.py tests/runtime/test_runner_gateway_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/conversations backend/app/runtime/execution_snapshot.py backend/alembic/versions/20260816_21_published_teams.py backend/tests/conversations backend/tests/runtime/test_execution_snapshot.py
git commit -m "feat: snapshot published team runs"
```

### Task 4: Bounded Team Supervisor Runtime

**Files:**
- Create: `backend/app/runtime/team_graph.py`
- Modify: `backend/app/runtime/sandbox_runtime.py`
- Modify: `backend/app/runtime/harness.py`
- Test: `backend/tests/runtime/test_team_graph.py`
- Test: `backend/tests/runtime/test_sandbox_runtime.py`
- Test: `backend/tests/runtime/test_harness.py`

**Interfaces:**
- Consumes: `PublishedTeamSnapshot`, existing gateway-backed model/tools, `DeepAgentFactory`, checkpoint/event clients, and cancellation state.
- Produces: `TeamPlan`, `TeamTask`, `validate_team_plan`, and `build_team_graph`.
- Emits: `team.plan.created`, `team.task.started/completed/failed`, and `team.synthesis.started/completed`.

- [ ] **Step 1: Write failing plan-boundary tests**

```python
def test_plan_rejects_unknown_member_cycle_and_excess_parallelism(team_snapshot):
    with pytest.raises(TeamPlanError, match="team_plan_invalid"):
        validate_team_plan(plan(member_id="outside"), team_snapshot)
    with pytest.raises(TeamPlanError, match="dependency cycle"):
        validate_team_plan(cyclic_plan(), team_snapshot)
    with pytest.raises(TeamLimitError, match="team_limit_exceeded"):
        validate_team_plan(parallel_plan(width=3), team_snapshot.model_copy(update={"max_parallel_members": 2}))


def test_parallel_results_join_in_task_position_order(team_runtime):
    result = team_runtime.run(plan=parallel_plan(completion_order=[2, 1]))
    assert [item.task_id for item in result.synthesis_inputs] == ["task-1", "task-2"]
```

- [ ] **Step 2: Run Team graph tests and confirm missing graph failure**

Run: `cd backend && pytest tests/runtime/test_team_graph.py -q`

Expected: FAIL because `team_graph` does not exist.

- [ ] **Step 3: Implement typed plan validation and bounded scheduler**

```python
class TeamTask(BaseModel):
    id: str
    member_id: str
    objective: str = Field(min_length=1, max_length=4000)
    depends_on: tuple[str, ...] = ()
    position: int = Field(ge=0)


class TeamPlan(BaseModel):
    tasks: tuple[TeamTask, ...]
```

Validate membership, unique IDs/positions, dependency existence, acyclic graph, `max_steps`, parallel width, and Runner `max_subagents`. Use platform-owned nodes only. Create member Deep Agents from embedded snapshots and route all capabilities through existing gateway adapters.

- [ ] **Step 4: Implement events, failure strategies, approval resume, and cancellation**

Test both `fail_fast` and `continue_then_synthesize`; mark partial synthesis explicitly. Verify approval checkpoints retain Team version and member/task identity. Check cancellation before dequeuing each task and before synthesis.

- [ ] **Step 5: Select runtime by actor kind and remove obsolete rejection**

```python
if snapshot.payload.actor.kind == "team":
    graph = build_team_graph(snapshot.payload.actor, clients=self.clients)
else:
    graph = self.deep_agent_factory.build(snapshot.payload.actor)
```

Do not add database, MinIO, Docker, provider, or MCP credentials to the Runner.

- [ ] **Step 6: Run focused and runtime regression suites**

Run: `cd backend && pytest tests/runtime/test_team_graph.py tests/runtime/test_sandbox_runtime.py tests/runtime/test_harness.py tests/runtime/test_gateway_model.py tests/runtime/test_gateway_tools.py tests/approvals -q`

Expected: PASS, including the former `unsupported_actor_type` test rewritten as a successful Team dispatch test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/runtime/team_graph.py backend/app/runtime/sandbox_runtime.py backend/app/runtime/harness.py backend/tests/runtime/test_team_graph.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_harness.py
git commit -m "feat: execute bounded multi-agent teams"
```

### Task 5: Team Management Frontend

**Files:**
- Create: `frontend/src/api/teams.ts`
- Create: `frontend/src/views/collaboration/TeamManageView.vue`
- Create: `frontend/src/views/collaboration/TeamManageView.test.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/router/routes.test.ts`

**Interfaces:**
- Consumes: Team API from Task 2 and `agentsApi.list()`.
- Produces: `teamsApi`, Team TypeScript contracts, and the `/collaboration` management page.

- [ ] **Step 1: Write failing API and view tests**

```ts
it('edits a draft with one supervisor, members and bounded settings', async () => {
  vi.mocked(teamsApi.list).mockResolvedValue([draftTeam]);
  vi.mocked(agentsApi.list).mockResolvedValue([supervisorAgent, reviewerAgent]);
  const wrapper = mount(TeamManageView);
  await flushPromises();
  await wrapper.get('[data-testid="team-edit"]').trigger('click');
  await wrapper.get('[data-testid="team-publish"]').trigger('click');
  expect(teamsApi.publish).toHaveBeenCalledWith(draftTeam.id);
});
```

Also assert readers cannot see draft controls, validation errors are field-specific, version history is read-only, and longest Chinese labels fit at mobile and desktop widths.

- [ ] **Step 2: Run frontend tests and confirm missing modules**

Run: `cd frontend && npm test -- --run src/views/collaboration/TeamManageView.test.ts src/router/routes.test.ts`

Expected: FAIL because Team API and view do not exist and the route is generic.

- [ ] **Step 3: Implement typed Team API**

```ts
export interface TeamSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  draft_revision: number;
  published_version: number | null;
  supervisor: TeamMemberInfo | null;
  member_count: number;
  updated_at: string;
}
```

Implement all routes from the design with the existing `request` client and encoded path parameters.

- [ ] **Step 4: Implement the management view and route**

Use an unframed operational layout with a dense table, filters, editor drawer/modal, explicit supervisor selector, member list, limit inputs, failure strategy menu, version history, and publication/toggle commands. Use Ant Design Vue controls and existing icons; no nested cards or placeholder Teams.

- [ ] **Step 5: Run view, route, type, and build checks**

Run: `cd frontend && npm test -- --run src/views/collaboration/TeamManageView.test.ts src/router/routes.test.ts && npm run build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/teams.ts frontend/src/views/collaboration/TeamManageView.vue frontend/src/views/collaboration/TeamManageView.test.ts frontend/src/router/routes.ts frontend/src/router/routes.test.ts
git commit -m "feat: add multi-agent team management"
```

### Task 6: Real Team Chat Selection And Progress

**Files:**
- Modify: `frontend/src/views/agent/AgentConsoleView.vue`
- Modify: `frontend/src/views/agent/AgentConsoleView.test.ts`
- Modify: `frontend/src/api/runEvents.ts`
- Modify: `frontend/src/features/chat/runtimeStatus.ts`
- Modify: `frontend/src/features/chat/runtimeStatus.test.ts`

**Interfaces:**
- Consumes: `teamsApi.list({ enabled: true, published: true })`, existing conversation store, SSE Run events, approval, cancellation, and Artifact UI.
- Produces: enabled Team mode, real Team/member display, Team event labels, and correct Team message payload.

- [ ] **Step 1: Write failing Team console tests**

```ts
it('selects a published team and sends the real team actor id', async () => {
  vi.mocked(teamsApi.list).mockResolvedValue([publishedTeam]);
  const wrapper = mountConsole();
  await flushPromises();
  await wrapper.get('[data-testid="mode-team"]').trigger('click');
  await wrapper.get('textarea').setValue('联合研判');
  await wrapper.get('[data-testid="send-message"]').trigger('click');
  expect(conversationsStore.sendMessage).toHaveBeenCalledWith(expect.any(String), {
    content: '联合研判', actor_type: 'team', actor_id: publishedTeam.id,
  });
});
```

Add tests for empty Team catalogue, disabled/unpublished filtering, member/task event rendering, partial synthesis, approval, cancellation, errors, and Artifact links.

- [ ] **Step 2: Run tests and confirm disabled placeholder behavior fails**

Run: `cd frontend && npm test -- --run src/views/agent/AgentConsoleView.test.ts src/features/chat/runtimeStatus.test.ts`

Expected: FAIL because Team mode is disabled and fixed Team arrays are used.

- [ ] **Step 3: Replace placeholders with API state**

Remove `teamOptions` and `teamMembers` constants. Load enabled published Teams, derive selected member details from the API response, reset invalid selection after refresh, and send Team actor payloads through the existing store.

- [ ] **Step 4: Add typed Team runtime events**

```ts
export type TeamRunEventType =
  | 'team.plan.created'
  | 'team.task.started'
  | 'team.task.completed'
  | 'team.task.failed'
  | 'team.synthesis.started'
  | 'team.synthesis.completed';
```

Render concise Chinese status labels including member name and task status without exposing prompts or raw traces.

- [ ] **Step 5: Run chat regressions and build**

Run: `cd frontend && npm test -- --run src/views/agent/AgentConsoleView.test.ts src/stores/conversations.test.ts src/api/runEvents.test.ts src/features/chat/runtimeStatus.test.ts && npm run build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/agent/AgentConsoleView.vue frontend/src/views/agent/AgentConsoleView.test.ts frontend/src/api/runEvents.ts frontend/src/features/chat/runtimeStatus.ts frontend/src/features/chat/runtimeStatus.test.ts
git commit -m "feat: enable published team chat runs"
```

### Task 7: End-To-End Acceptance And Documentation

**Files:**
- Create: `tests/e2e/test_published_team_run.py`
- Modify: `backend/README.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-16-published-team-foundation-design.md` only if implementation exposes a verified contract correction.

**Interfaces:**
- Consumes: all prior public APIs, conversation Run lifecycle, Runner Gateway seams, approval, cancellation, audit, and Artifact APIs.
- Produces: reproducible acceptance coverage and operator/developer commands.

- [ ] **Step 1: Write the failing acceptance scenario**

```python
def test_published_team_run_is_scoped_audited_and_reproducible(platform_client):
    team = create_team(platform_client, supervisor="forecast", members=["review", "report"])
    version = publish_and_enable(platform_client, team.id)
    run = send_team_message(platform_client, team.id, "联合研判未来洪峰")
    events = wait_for_terminal_events(platform_client, run.id)
    assert count(events, "team.task.completed") >= 2
    assert count(events, "team.synthesis.completed") == 1
    assert run_details(platform_client, run.id).actor_version_id == version.id
    assert all_artifacts_have_member_task_provenance(platform_client, run.id)
```

Add separate assertions for one approved Tool invocation, cancellation of another Team Run, cross-project `404`, and missing `collaboration.run` rejection.

- [ ] **Step 2: Run acceptance and fix only contract-level integration gaps**

Run: `pytest tests/e2e/test_published_team_run.py -q`

Expected before final wiring: FAIL at the first uncovered integration seam. Make only focused wiring corrections in the owning prior module and add a regression test beside each correction.

- [ ] **Step 3: Run full verification**

Run: `cd backend && pytest -q`

Expected: PASS.

Run: `cd frontend && npm test -- --run && npm run build`

Expected: PASS.

Run: `pytest tests/e2e/test_published_team_run.py -q`

Expected: PASS.

- [ ] **Step 4: Verify migration and security boundaries**

Run the repository's PostgreSQL migration suite, Runner Gateway nginx-boundary test, secret-redaction tests, and cross-project authorization tests. Confirm the Runner receives no database, MinIO administrator, Docker socket, provider, or MCP credentials.

- [ ] **Step 5: Update operator/developer documentation**

Document Team API endpoints, required permissions, publication semantics, how to run focused tests, and how disabled Teams/resources affect new and in-flight Runs. Do not document excluded visual workflow or device-control features as available.

- [ ] **Step 6: Commit acceptance and docs**

```bash
git add tests/e2e/test_published_team_run.py backend/README.md README.md
git diff --cached --name-status
git commit -m "test: verify published team execution"
```

Commit any integration correction in its owning earlier task with its focused regression test. This acceptance commit contains only the end-to-end test and documentation.

---

## Final Review Checklist

- [ ] Every Team lookup is unit/project scoped and cross-project access returns `404`.
- [ ] Draft conflicts return `409` without overwriting another editor's changes.
- [ ] Published versions and embedded Agent definitions remain immutable.
- [ ] Conversation acceptance records the exact Team version before dispatch.
- [ ] Team snapshots are canonical, digest-verified, size-bounded, and backward compatible with Agent snapshots.
- [ ] The supervisor cannot add members, capabilities, steps, parallelism, or recursion beyond published and Runner limits.
- [ ] Tool Gateway recalculates authorization and current availability for every member invocation.
- [ ] Approval, cancellation, checkpoints, events, and Artifacts remain Run-scoped and idempotent.
- [ ] Team mode contains no fixed sample data and handles an empty catalogue.
- [ ] Backend, frontend, build, migration, security-boundary, and end-to-end suites pass.
- [ ] Each implementation stage is committed independently without unrelated workspace changes.
