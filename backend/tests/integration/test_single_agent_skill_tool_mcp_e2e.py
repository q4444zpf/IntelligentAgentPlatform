"""Acceptance of immutable Skill execution across real runtime boundaries."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, delete, event, update

from app.agents.schemas import SkillBinding
from app.db.platform_models import RegisteredToolRecord
from app.mcp.store import McpStore
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.execution_snapshot import ExecutionSnapshotService, RuntimeExecutionSnapshot
from app.runtime.execution_snapshot import SkillUnavailableError, canonical_snapshot_bytes
from app.runtime.gateway_tools import build_skill_resource_tools, RunnerGatewayToolError
from app.runtime.model_gateway import ModelResult
from app.runtime.runner_gateway_auth import RunnerGatewayError, runner_gateway_error_handler
from app.runtime.runner_gateway_client import RunnerGatewayClient
from app.runtime.runner_gateway_client import RunnerGatewayBusinessError
from app.runtime.runner_gateway_router import create_router
from app.runtime.sandbox_runtime import SandboxRuntime
from app.skills.package import parse_skill_bundle
from app.skills.package_storage import SkillPackageStorage
from app.skills.repository import SkillRepository, SkillScope
from app.skills.models import Skill
from app.tools.schemas import ToolCall
from app.tools.service import ToolService
from tests.skills.test_package_storage import MemoryS3, make_bundle

from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, RunEvent, ToolInvocation
from tests.runtime.test_run_lifecycle import FakeTokens, SequenceRunner, make_coordinator
from tests.runtime.test_script_terminal_lifecycle import script_factory
from tests.runtime.test_team_graph import snapshot as team_snapshot


SKILL_ID = "ce905a71-a715-48d1-8706-913d91c74932"
MCP_TOOL = "mcp.water.level"
RESOURCE_TOOL = "skill.forecast.resource.read"
SCRIPT_TOOL = "skill.forecast.script.normalize"
INSTRUCTIONS = "Read rules and result template, normalize water level, then report FORECAST-VERIFIED."
FINAL_ANSWER = "FORECAST-VERIFIED: level=8; rule=add-one; source=water-mcp"


class CompletionBoundary:
    """Only remote completion is deterministic; tool messages come from the graph."""

    def __init__(self):
        self.requests = []

    def generate(self, messages, selection=None, tools=None):
        self.requests.append((messages, tools))
        calls = [
            (RESOURCE_TOOL, {"path": "references/rules.txt"}),
            (RESOURCE_TOOL, {"path": "templates/result.json"}),
            ("system.get_current_time", {"timezone": "UTC"}),
            (MCP_TOOL, {"station": "gate-1"}),
            (SCRIPT_TOOL, {"value": 7}),
        ]
        index = len(self.requests) - 1
        if index < len(calls):
            name, arguments = calls[index]
            return ModelResult(None, 10, 2, 12, (ToolCall(f"acceptance-{index}", name, arguments),))
        return ModelResult(FINAL_ANSWER, 20, 5, 25)


class McpTransportBoundary:
    def call_tool(self, url, transport, headers, name, arguments, **kwargs):
        assert (url, transport, headers, name, arguments) == (
            "https://water.example.test/mcp", "streamable_http", {}, "level", {"station": "gate-1"},
        )
        return {"isError": False, "content": [{"type": "text", "text": "water-mcp level=7"}]}


@pytest.fixture
def skill_run(runner_gateway_env, tmp_path):
    env = runner_gateway_env
    session = env.session
    factory = env.tool_store.session_factory
    declaration = {
        "name": "normalize", "path": "scripts/normalize.py",
        "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False},
        "timeout_seconds": 5,
    }
    manifest = "---\n" + yaml.safe_dump({
        "name": "forecast", "description": "Forecast acceptance", "version": "2.7.0",
        "metadata": {"scripts": [declaration]},
    }) + "---\n" + INSTRUCTIONS + "\n"
    files = [
        ("forecast/SKILL.md", manifest.encode()),
        ("forecast/references/rules.txt", b"add-one: normalized level equals input plus one"),
        ("forecast/templates/result.json", b'{"level":0,"rule":"add-one","source":"water-mcp"}'),
        ("forecast/scripts/normalize.py", b"import json,sys\njson.dump({'value': json.load(sys.stdin)['value'] + 1}, sys.stdout)\n"),
    ]
    package = parse_skill_bundle(make_bundle(files))[0]
    memory_s3 = MemoryS3()
    storage = SkillPackageStorage(memory_s3, "skill-acceptance")
    stored = storage.put("unit-1", "project-1", SKILL_ID, package)
    repository = SkillRepository(session)
    scope = SkillScope("unit-1", "project-1")
    repository.create(scope, skill_id=SKILL_ID, name="forecast", created_by="user-1", package=package, stored=stored)
    version = repository.publish(scope, SKILL_ID, expected_revision=1, idempotency_key="publish-acceptance", published_by="user-1")
    session.add(RegisteredToolRecord(
        tool_id=MCP_TOOL, version="1", name="level", description="Read water level",
        source="mcp", risk_level="low", input_schema={"type": "object", "properties": {"station": {"type": "string"}}, "required": ["station"]},
        output_schema={"type": "object"}, source_resource_id="water", source_capability_id="level",
        source_available=True, requires_approval=False, published=True, enabled=True,
    ))
    session.execute(delete(RuntimeExecutionSnapshot).where(RuntimeExecutionSnapshot.run_id == "run-1"))
    session.commit()
    McpStore(factory).create("water", {"name": "Water", "description": "Water", "url": "https://water.example.test/mcp", "transport": "streamable_http", "headers": {}, "enabled": True})
    agent = SimpleNamespace(
        id="agent-1", name="Acceptance", description="", runtime_form="common", language="zh-CN",
        system_prompt="Use authorized tools.", context_prompt="", approval_policy="never", enabled=True,
        provider_id="test-provider", model="test-model", tool_ids=["system.get_current_time", MCP_TOOL],
        knowledge_source_ids=[], skill_names=["forecast"],
        skill_bindings=[SkillBinding(skill_id=SKILL_ID, version_id=version.id, name="forecast")],
    )
    snapshots = ExecutionSnapshotService(session, SimpleNamespace(get=lambda _: agent, tool_service=ToolService(env.tool_store)), env.repository, max_iterations=8)
    snapshot = snapshots.create("run-1")
    env.snapshots["run-1"] = snapshot
    completion = CompletionBoundary()
    env.tool_gateway.mcp_store = McpStore(factory)
    env.tool_gateway.mcp_protocol_client = McpTransportBoundary()
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(create_router(
        token_service_dependency=lambda: env.token_service,
        snapshot_service_dependency=lambda: snapshots,
        checkpoint_store_dependency=lambda: env.checkpoint_store,
        conversation_repository_dependency=lambda: env.repository,
        model_gateway_dependency=lambda: completion,
        tool_gateway_dependency=lambda: env.tool_gateway,
        artifact_service_dependency=lambda: env.artifacts,
        skill_package_storage_dependency=lambda: storage,
    ), prefix="/internal/runner")
    api = TestClient(app)
    token = env.issue_token(actions={"snapshot.read", "model.invoke", "tool.invoke", "checkpoint.read", "checkpoint.write", "event.append", "artifact.create", "result.complete", "skill.resource.read", "skill.script.execute"})

    def transport(request):
        response = api.request(request.method, request.url.raw_path.decode("ascii"), content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    gateway = RunnerGatewayClient("http://api/internal/runner", "run-1", token, transport=httpx.MockTransport(transport))
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    execution = RunExecutionRequest(
        run_id="run-1", agent_version="agent-1", checkpoint_key="runtime", deadline_at=deadline,
        execution_deadline_at=deadline, snapshot_id=snapshot.snapshot_id, snapshot_digest=snapshot.digest,
        gateway_url="http://api/internal/runner", run_token=token,
    )
    yield SimpleNamespace(env=env, snapshots=snapshots, snapshot=snapshot, completion=completion, gateway=gateway,
                          request=execution, workspace=tmp_path / "runner", agent=agent, storage=storage, memory_s3=memory_s3)
    api.close()


def test_published_skill_resources_script_builtin_and_mcp_complete_one_real_graph(skill_run):
    with pytest.raises(RunnerGatewayBusinessError) as rejected_resource:
        skill_run.gateway.read_skill_file("forecast", "undeclared.txt")
    assert rejected_resource.value.code == "skill_resource_not_found"
    runtime = SandboxRuntime(skill_run.gateway, workspace=skill_run.workspace)
    result = runtime.execute(skill_run.request)
    assert result.status == "completed", result.error_code
    requests = skill_run.completion.requests
    assert len(requests) == 6
    assert INSTRUCTIONS in requests[0][0][0]["content"]
    assert {RESOURCE_TOOL, SCRIPT_TOOL, MCP_TOOL, "system.get_current_time"} <= {tool.tool_id for tool in requests[0][1]}
    history = requests[-1][0]
    tool_messages = {item["tool_call_id"]: item["content"] for item in history if item["role"] == "tool"}
    assert "add-one: normalized" in tool_messages["acceptance-0"]
    assert json.loads(json.loads(tool_messages["acceptance-1"])["content"])["source"] == "water-mcp"
    assert "UTC" in tool_messages["acceptance-2"]
    assert "water-mcp level=7" in tool_messages["acceptance-3"]
    assert json.loads(tool_messages["acceptance-4"]) == {"value": 8}
    assert (skill_run.workspace / "skills/forecast/references/rules.txt").read_text() == "add-one: normalized level equals input plus one"
    session = skill_run.env.session
    session.expire_all()
    assert session.get(AgentRun, "run-1").status == "completed"
    invocations = list(session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == "run-1")))
    assert {item.tool_id for item in invocations} == {SCRIPT_TOOL, MCP_TOOL, "system.get_current_time"}
    assert all(item.status in {"completed", "succeeded"} for item in invocations)
    from app.conversations.models import Message
    assert session.scalar(select(Message.content).where(Message.role == "assistant")) == FINAL_ANSWER
    audits = list(session.scalars(select(AuditEvent).where(AuditEvent.run_id == "run-1")))
    actions = {item.action for item in audits}
    assert {"skill.resource.read", "skill.resource.failed", "skill.script.started", "skill.script.completed", "tool.invoke.started", "tool.invoke.succeeded", "runner.run.completed"} <= actions
    assert {item.resource_name for item in audits if item.action == "skill.resource.read"} >= {"references/rules.txt", "templates/result.json"}
    assert {item.resource_id for item in audits if item.action == "tool.invoke.succeeded"} == {MCP_TOOL, "system.get_current_time"}
    assert sum(item.action == "runner.run.completed" for item in audits) == 1


def test_resource_read_persists_run_audit(skill_run):
    response = skill_run.gateway.read_skill_file("forecast", "references/rules.txt")
    assert response["data"] == b"add-one: normalized level equals input plus one"
    audits = list(skill_run.env.session.scalars(select(AuditEvent).where(
        AuditEvent.run_id == "run-1", AuditEvent.action == "skill.resource.read",
    )))
    assert len(audits) == 1
    assert audits[0].resource_name == "references/rules.txt"


def test_resource_storage_read_does_not_hold_run_terminal_lock(skill_run, monkeypatch):
    locked_queries = []
    session = skill_run.env.session

    def capture_lock(execution):
        if getattr(execution.statement, "_for_update_arg", None) is not None:
            locked_queries.append(execution.statement)

    original_read = skill_run.storage.read

    def read_without_lock(stored):
        assert locked_queries == [], "Object storage must not block the run terminal lock"
        return original_read(stored)

    event.listen(session, "do_orm_execute", capture_lock)
    monkeypatch.setattr(skill_run.storage, "read", read_without_lock)
    try:
        response = skill_run.gateway.read_skill_file("forecast", "references/rules.txt")
    finally:
        event.remove(session, "do_orm_execute", capture_lock)
    assert response["data"] == b"add-one: normalized level equals input plus one"
    assert locked_queries


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [("cancelled", "run_not_active"), ("failed", "run_not_active"),
     ("completed", "run_not_active"), ("revoked", "run_token_invalid"),
     ("snapshot", "snapshot_invalid")],
)
def test_resource_read_rechecks_authority_after_storage_read(skill_run, monkeypatch, change, expected_code):
    original_read = skill_run.storage.read
    retained_snapshot_row = skill_run.env.session.get(RuntimeExecutionSnapshot, skill_run.snapshot.snapshot_id)
    assert retained_snapshot_row is not None

    def read_during_authority_change(stored):
        data = original_read(stored)
        if change == "revoked":
            skill_run.env.token_service.revoke("run-1", "test-resource-revocation")
        else:
            with skill_run.env.tool_store.session_factory.begin() as session:
                if change == "snapshot":
                    session.execute(update(RuntimeExecutionSnapshot).where(
                        RuntimeExecutionSnapshot.run_id == "run-1",
                    ).values(digest="0" * 64))
                else:
                    session.execute(update(AgentRun).where(AgentRun.id == "run-1").values(status=change))
        return data

    monkeypatch.setattr(skill_run.storage, "read", read_during_authority_change)
    with pytest.raises(RunnerGatewayBusinessError) as failure:
        skill_run.gateway.read_skill_file("forecast", "references/rules.txt")
    assert failure.value.code == expected_code
    audits = list(skill_run.env.session.scalars(select(AuditEvent).where(
        AuditEvent.run_id == "run-1", AuditEvent.action.like("skill.resource.%"),
    )))
    assert len(audits) == 1
    assert audits[0].action == "skill.resource.failed"
    assert audits[0].error_code == expected_code


@pytest.mark.parametrize("disable_when", ["before_read", "during_read"])
def test_resource_read_denies_published_skill_disabled_after_snapshot(skill_run, monkeypatch, disable_when):
    frozen_skill = skill_run.snapshot.payload.skills[0]
    assert frozen_skill.enabled is True
    assert frozen_skill.skill_id == SKILL_ID
    assert frozen_skill.version_id is not None

    def disable_skill():
        with skill_run.env.tool_store.session_factory.begin() as session:
            SkillRepository(session).set_enabled(
                SkillScope("unit-1", "project-1"), SKILL_ID,
                enabled=False, expected_revision=2,
            )

    if disable_when == "before_read":
        disable_skill()
    else:
        original_read = skill_run.storage.read

        def read_while_skill_is_disabled(stored):
            data = original_read(stored)
            disable_skill()
            return data

        monkeypatch.setattr(skill_run.storage, "read", read_while_skill_is_disabled)

    with pytest.raises(RunnerGatewayBusinessError) as failure:
        skill_run.gateway.read_skill_file("forecast", "references/rules.txt")
    assert failure.value.code == "skill_unavailable"
    assert skill_run.snapshots.get(skill_run.snapshot.snapshot_id).payload.skills[0] == frozen_skill
    with skill_run.env.tool_store.session_factory() as session:
        audits = list(session.scalars(select(AuditEvent).where(
            AuditEvent.run_id == "run-1", AuditEvent.action.like("skill.resource.%"),
        )))
        assert len(audits) == 1
        assert audits[0].action == "skill.resource.failed"
        assert audits[0].error_code == "skill_unavailable"
        assert audits[0].resource_name == "references/rules.txt"


def test_resource_read_preserves_legacy_team_snapshot_without_project_skill_ids(skill_run, team_snapshot):
    session = skill_run.env.session
    snapshot = skill_run.snapshot
    skill = snapshot.payload.skills[0].model_copy(update={
        "skill_id": None, "version_id": None, "version": "2.7.0",
    })
    payload = snapshot.payload.model_copy(update={
        "schema_version": "5", "actor": team_snapshot, "skills": (skill,),
    })
    row = session.get(RuntimeExecutionSnapshot, snapshot.snapshot_id)
    row.payload = payload.model_dump(mode="json")
    row.digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    run = session.get(AgentRun, "run-1")
    run.actor_type = "team"
    run.actor_id = team_snapshot.id
    session.commit()
    skill_run.env.snapshots["run-1"] = skill_run.snapshots.get(snapshot.snapshot_id)
    skill_run.gateway.token = skill_run.env.issue_token(actions={"skill.resource.read"})

    response = skill_run.gateway.read_skill_file("forecast", "references/rules.txt")
    assert response["data"] == b"add-one: normalized level equals input plus one"
    with skill_run.env.tool_store.session_factory() as audit_session:
        assert audit_session.scalar(select(AuditEvent.action).where(
            AuditEvent.run_id == "run-1", AuditEvent.action.like("skill.resource.%"),
        )) == "skill.resource.read"


def test_rejected_resource_controls_remain_database_safe_and_auditable(skill_run):
    path = "references/\x00bad.txt"
    session = skill_run.env.session

    def assert_safe(value):
        if isinstance(value, str):
            assert not any(ord(character) < 32 or ord(character) == 127 for character in value)
        elif isinstance(value, dict):
            for item in value.values():
                assert_safe(item)

    def enforce_database_text(_session, _context, _instances):
        for row in _session.new:
            if isinstance(row, AuditEvent):
                assert_safe(row.resource_name)
                assert_safe(row.resource_id)
                assert_safe(row.metadata_json)
            elif isinstance(row, RunEvent) and row.event_type.startswith("skill.resource."):
                assert_safe(row.payload)

    event.listen(session, "before_flush", enforce_database_text)
    try:
        with pytest.raises(RunnerGatewayBusinessError) as failure:
            skill_run.gateway.read_skill_file("forecast", path)
    finally:
        event.remove(session, "before_flush", enforce_database_text)
    assert failure.value.code == "skill_resource_not_found"
    audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "skill.resource.failed"))
    assert audit.metadata_json["path_sha256"] == hashlib.sha256(path.encode()).hexdigest()
    assert "\x00" not in audit.resource_name


def test_completion_persists_one_terminal_run_audit(skill_run):
    request = {"status": "completed", "final_assistant_content": "done", "artifact_refs": []}
    skill_run.gateway.complete(request, "acceptance-complete")
    skill_run.gateway.complete(request, "acceptance-complete")
    audits = list(skill_run.env.session.scalars(select(AuditEvent).where(
        AuditEvent.run_id == "run-1", AuditEvent.action == "runner.run.completed",
    )))
    assert len(audits) == 1


@pytest.mark.parametrize(("status", "code"), [("failed", "sandbox_failed"), ("failed", "sandbox_timeout"), ("cancelled", "sandbox_cancelled")])
def test_gateway_terminal_completion_closes_inflight_script_lease(skill_run, status, code):
    lease = skill_run.gateway.execute_script(script_name=SCRIPT_TOOL, arguments={"value": 7}, tool_call_id="inflight", invocation_sequence=0, idempotency_key="inflight")
    request = {"status": status, "error_code": code, "artifact_refs": []}
    skill_run.gateway.complete(request, "terminal")
    skill_run.gateway.complete(request, "terminal")
    session = skill_run.env.session
    session.expire_all()
    invocation = session.get(ToolInvocation, lease["lease_id"])
    assert invocation.status == status
    assert invocation.completed_at is not None
    audits = list(session.scalars(select(AuditEvent).where(AuditEvent.run_id == "run-1", AuditEvent.action == f"skill.script.{status}")))
    assert len(audits) == 1


@pytest.mark.parametrize("path", ["../private.txt", "/etc/passwd", "C:/secret", "references/../../secret", "unknown.txt"])
def test_resource_tool_rejects_paths_outside_authorized_manifest(skill_run, path):
    tool = build_skill_resource_tools(skill_run.snapshot.payload, skill_run.gateway)[0]
    with pytest.raises(RunnerGatewayToolError):
        tool.invoke({"path": path})
    assert skill_run.completion.requests == []


def test_gateway_rejects_unbound_resource_and_records_safe_failure(skill_run):
    with pytest.raises(RunnerGatewayBusinessError) as failure:
        skill_run.gateway.read_skill_file("other-skill", "references/rules.txt")
    assert failure.value.code == "skill_resource_not_found"
    audit = skill_run.env.session.scalar(select(AuditEvent).where(AuditEvent.action == "skill.resource.failed"))
    assert audit.run_id == "run-1"
    assert audit.error_code == "skill_resource_not_found"


def test_long_resource_path_failure_remains_auditable(skill_run):
    path = "references/" + "long-name-" * 25 + ".txt"
    with pytest.raises(RunnerGatewayBusinessError) as failure:
        skill_run.gateway.read_skill_file("forecast", path)
    assert failure.value.code == "skill_resource_not_found"
    audit = skill_run.env.session.scalar(select(AuditEvent).where(AuditEvent.action == "skill.resource.failed"))
    assert audit.run_id == "run-1"
    assert audit.metadata_json["path_sha256"] == hashlib.sha256(path.encode()).hexdigest()


def test_gateway_rejects_undeclared_script_without_creating_lease(skill_run):
    with pytest.raises(RunnerGatewayBusinessError) as failure:
        skill_run.gateway.execute_script(script_name="skill.forecast.script.undeclared", arguments={}, tool_call_id="undeclared", invocation_sequence=0, idempotency_key="undeclared")
    assert failure.value.code == "skill_script_not_authorized"
    assert skill_run.env.session.scalar(select(ToolInvocation).where(ToolInvocation.run_id == "run-1")) is None


@pytest.mark.parametrize("tamper", ["package", "file"])
def test_tampered_skill_bytes_fail_before_model_invocation(skill_run, tamper):
    if tamper == "package":
        record = next(iter(skill_run.memory_s3.objects.values()))
        record["Body"] = b"x" * len(record["Body"])
    else:
        snapshot = skill_run.snapshot
        skill = snapshot.payload.skills[0]
        files = tuple(item.model_copy(update={"sha256": "0" * 64}) if item.path == "references/rules.txt" else item for item in skill.files)
        payload = snapshot.payload.model_copy(update={"skills": (skill.model_copy(update={"files": files}),)})
        digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
        row = skill_run.env.session.get(RuntimeExecutionSnapshot, snapshot.snapshot_id)
        row.payload = payload.model_dump(mode="json")
        row.digest = digest
        skill_run.env.session.commit()
        stored = skill_run.snapshots.get(snapshot.snapshot_id)
        skill_run.env.snapshots["run-1"] = stored
        token = skill_run.env.issue_token(actions={"snapshot.read", "skill.resource.read", "result.complete"})
        skill_run.gateway.token = token
        skill_run.request = skill_run.request.model_copy(update={"snapshot_digest": digest, "run_token": token})
    result = SandboxRuntime(skill_run.gateway, workspace=skill_run.workspace).execute(skill_run.request)
    assert result.status == "failed"
    assert result.error_code == "skill_resource_invalid"
    assert skill_run.completion.requests == []
    assert not (skill_run.workspace / "skills").exists()
    audits = list(skill_run.env.session.scalars(select(AuditEvent).where(AuditEvent.run_id == "run-1")))
    assert {"skill.resource.failed", "runner.run.failed"} <= {item.action for item in audits}


@pytest.mark.parametrize("unavailable", ["disabled", "missing", "unpublished", "legacy"])
def test_unavailable_binding_fails_before_model_invocation(skill_run, unavailable):
    session = skill_run.env.session
    session.execute(delete(RuntimeExecutionSnapshot).where(RuntimeExecutionSnapshot.run_id == "run-1"))
    skill = session.get(Skill, SKILL_ID)
    if unavailable == "disabled":
        skill.enabled = False
    elif unavailable == "unpublished":
        draft_id = str(uuid4())
        session.add(Skill(id=draft_id, unit_id="unit-1", project_id="project-1", name="unpublished", created_by="user-1"))
        skill_run.agent.skill_names = ["unpublished"]
        skill_run.agent.skill_bindings = [SkillBinding(skill_id=draft_id, version_id=str(uuid4()), name="unpublished")]
    elif unavailable == "missing":
        skill_run.agent.skill_bindings = [SkillBinding(skill_id=SKILL_ID, version_id=str(uuid4()), name="forecast")]
    else:
        skill_run.agent.skill_bindings = []
    session.commit()
    with pytest.raises(SkillUnavailableError):
        skill_run.snapshots.create("run-1")
    assert skill_run.completion.requests == []


def test_script_output_schema_failure_rejects_wrong_value_type(tmp_path):
    from app.runtime.skill_scripts import SkillScriptError, execute_script, load_script_specs
    from app.runtime.execution_snapshot import SnapshotSkill

    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/normalize.py").write_text("print('{\"value\": \"wrong-type\"}')", encoding="utf-8")
    skill = SnapshotSkill(name="forecast", metadata={"scripts": [{
        "name": "normalize", "path": "scripts/normalize.py", "timeout_seconds": 5,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
    }]})
    with pytest.raises(SkillScriptError, match="output"):
        execute_script(load_script_specs(skill)[0], root, {})


@pytest.mark.parametrize(
    ("outcome", "script_status", "error_code"),
    [
        ("timeout", "failed", "skill_script_timeout"),
        ("failed", "failed", "skill_script_failed"),
        ("cancelled", "cancelled", "skill_script_cancelled"),
    ],
)
def test_run_terminal_finalizes_script_before_token_revocation(
    script_factory, outcome, script_status, error_code,
):
    # Losing the coordinator's lease finalization must fail at token revocation.
    class Tokens(FakeTokens):
        def revoke(self, run_id, reason):
            with script_factory() as session:
                lease = session.get(ToolInvocation, "lease-1")
                assert lease.status == script_status
                assert lease.error_code == error_code
                assert lease.completed_at is not None
            super().revoke(run_id, reason)

    with script_factory.begin() as session:
        session.get(AgentRun, "run-1").status = "queued"
    tokens = Tokens()
    runner = SequenceRunner([{"status": "exited", "exit_code": 2}])
    options = {"poll_interval": 0, "tokens": tokens}
    if outcome == "timeout":
        ticks = iter([0.0, 0.5, 1.1, 1.1])
        options.update(timeout_seconds=1, monotonic=lambda: next(ticks))
        runner = SequenceRunner([{"status": "running"}])
    coordinator = make_coordinator(script_factory, runner, **options)
    if outcome == "cancelled":
        coordinator.cancel("run-1")
    else:
        coordinator.execute("run-1")
    coordinator.cancel("run-1")
    coordinator.recover("run-1")

    with script_factory() as session:
        lease = session.get(ToolInvocation, "lease-1")
        events = list(session.scalars(select(RunEvent).where(
            RunEvent.run_id == "run-1", RunEvent.event_type.like("skill.script.%"),
        )))
        audits = list(session.scalars(select(AuditEvent).where(
            AuditEvent.run_id == "run-1", AuditEvent.action.like("skill.script.%"),
        )))
        assert lease.status == script_status
        assert len(events) == len(audits) == 1
        assert events[0].event_type == audits[0].action == f"skill.script.{script_status}"
        assert audits[0].resource_id == "lease-1"
        assert len(tokens.revoked) == 1
