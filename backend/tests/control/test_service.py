from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.artifacts.models import ArtifactRecord
from app.core.request_context import RequestContext
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.db.base import Base
from app.control.schemas import (
    ControlCommandProposeRequest,
    ControlFeedbackRequest,
    ControlPolicyCreateRequest,
)
from app.control.service import (
    ControlExecutionError,
    ControlForbiddenError,
    ControlValidationError,
    ControlIdempotencyConflict,
    ControlService,
)


class FakeExecutor:
    def __init__(self, result=None):
        self.result = result or {"outcome": "succeeded", "external_id": "ext-1"}
        self.calls = []

    def execute(self, command, external_idempotency_key):
        self.calls.append(("execute", command.id, external_idempotency_key))
        return self.result

    def rollback(self, command, rollback_plan, external_idempotency_key):
        self.calls.append(("rollback", command.id, external_idempotency_key))
        return self.result


def _context(user_id="operator", project_id="project-1", roles=("user",)):
    grants = ()
    if "project_admin" in roles:
        grants = tuple(PermissionGrant(code, "project", frozenset({project_id}), None) for code in ("control.manage", "control.propose", "control.approve", "control.execute", "control.feedback"))
    elif user_id == "operator":
        grants = (PermissionGrant("control.propose", "project", frozenset({project_id}), None), PermissionGrant("control.feedback", "project", frozenset({project_id}), None))
    authorization = AuthorizationContext(session_id="test", user_id=user_id, unit_id="unit-1", current_project_id=project_id, auth_method="dev_test", authorization_version=1, role_codes=tuple(roles), grants=grants)
    return RequestContext(
        user_id=user_id,
        unit_id="unit-1",
        project_id=project_id,
        roles=frozenset(roles),
        authorization_context=authorization,
    )


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'control.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _artifact(session, *, project_id="project-1", run_id=None, success=True):
    from app.collaboration.models import Team, TeamVersion
    from app.conversations.models import AgentRun, Conversation, Message
    run_id = run_id or "simulation-run"
    team = Team(id="simulation-team", unit_id="unit-1", project_id=project_id, name="Simulation")
    version = TeamVersion(id="simulation-version", team_id=team.id, version=1, status="published", definition_digest="d" * 64)
    conversation = Conversation(id="simulation-conversation", unit_id="unit-1", project_id=project_id, owner_id="operator", title="simulation")
    message = Message(id="simulation-message", conversation_id=conversation.id, sequence=1, role="user", content="simulate")
    run = AgentRun(id=run_id, conversation_id=conversation.id, trigger_message_id=message.id, actor_type="team", actor_id=team.id, actor_version_id=version.id, status="completed")
    session.add_all([team, version, conversation, message, run])
    artifact = ArtifactRecord(
        id="artifact-1" if project_id == "project-1" else "artifact-2",
        unit_id="unit-1",
        project_id=project_id,
        owner_id="operator",
        scope="project",
        run_id=run_id,
        object_key=f"objects/{project_id}",
        filename="simulation.json",
        content_type="application/json",
        size_bytes=2,
        sha256="a" * 64,
        provenance={"team_version_id": version.id, "member_agent_id": "simulator", "task_id": "simulation", "invocation_id": "simulation-invocation", "simulation_attestation": {"kind": "simulation", "status": "succeeded" if success else "failed", "completed_at": "2026-09-15T10:00:00Z", "artifact_sha256": "a" * 64}},
        status="active",
    )
    session.add(artifact)
    session.commit()
    return artifact


@pytest.mark.parametrize("forgery", ["loose", "digest", "upload", "missing_invocation", "failed"])
def test_propose_rejects_forged_simulation_metadata(session, forgery):
    service = ControlService(session)
    policy = service.create_policy(_context("admin", roles=("project_admin",)), _policy_request())
    artifact = _artifact(session)
    if forgery == "loose":
        artifact.provenance = {"kind": "simulation", "status": "succeeded"}
    elif forgery == "digest":
        artifact.sha256 = "b" * 64
    elif forgery == "upload":
        artifact.run_id = None
    elif forgery == "missing_invocation":
        artifact.provenance = {k: v for k, v in artifact.provenance.items() if k != "invocation_id"}
    else:
        artifact.provenance = {**artifact.provenance, "simulation_attestation": {**artifact.provenance["simulation_attestation"], "status": "failed"}}
    session.commit()
    with pytest.raises(ControlValidationError):
        service.propose(_context(), _propose_request(policy.id))


def test_propose_accepts_gateway_attestation_bound_to_same_run_and_digest(session):
    from app.artifacts.service import ArtifactService
    service = ControlService(session)
    policy = service.create_policy(_context("admin", roles=("project_admin",)), _policy_request())
    artifact = _artifact(session)
    artifact.provenance = {k: v for k, v in artifact.provenance.items() if k != "simulation_attestation"}
    session.commit()
    ArtifactService(session, None).attest_completed_simulation(artifact.run_id, {"run_id": artifact.run_id, **artifact.provenance}, (artifact.id,), datetime.now(timezone.utc))
    command = service.propose(_context(), _propose_request(policy.id, run_id=artifact.run_id, team_version_id="simulation-version"))
    assert command.status == "pending_approval"
    assert command.simulation_digest == artifact.sha256


@pytest.mark.parametrize("binding", [{"run_id": "simulation-run"}, {"team_version_id": "simulation-version"}, {"run_id": "other-run", "team_version_id": "simulation-version"}, {"run_id": "simulation-run", "team_version_id": "other-version"}, {"run_id": "simulation-run", "team_version_id": "draft-version"}])
def test_propose_rejects_mismatched_or_unpublished_team_version_id(session, binding):
    from app.collaboration.models import TeamVersion
    from app.conversations.models import AgentRun
    service = ControlService(session)
    policy = service.create_policy(_context("admin", roles=("project_admin",)), _policy_request())
    _artifact(session)
    if binding.get("team_version_id") == "draft-version":
        session.add(TeamVersion(id="draft-version", team_id="simulation-team", version=0, status="draft"))
        session.get(AgentRun, "simulation-run").actor_version_id = "draft-version"
        artifact = session.get(ArtifactRecord, "artifact-1")
        artifact.provenance = {**artifact.provenance, "team_version_id": "draft-version"}
        session.commit()
    with pytest.raises(ControlValidationError):
        service.propose(_context(), _propose_request(policy.id, **binding))


def _policy_request(**overrides):
    values = {
        "asset_type": "reservoir",
        "command_type": "release",
        "parameter_schema": {
            "type": "object",
            "properties": {"amount": {"type": "number", "minimum": 0, "maximum": 100}},
            "required": ["amount"],
            "additionalProperties": False,
        },
        "max_command_lifetime_seconds": 3600,
        "approval_mode": "single",
        "deviation_thresholds": {"amount": 5},
    }
    values.update(overrides)
    return ControlPolicyCreateRequest(**values)


def _propose_request(policy_id, **overrides):
    now = datetime.now(timezone.utc)
    values = {
        "policy_id": policy_id,
        "target": {"asset_id": "reservoir-1", "asset_type": "reservoir"},
        "parameters": {"amount": 20},
        "simulation_artifact_id": "artifact-1",
        "valid_from": now,
        "valid_until": now + timedelta(minutes=10),
        "rollback_plan": {"amount": 0},
        "expected_state": {"amount": 20},
        "idempotency_key": "proposal-1",
    }
    values.update(overrides)
    return ControlCommandProposeRequest(**values)


def test_project_isolation_and_json_schema_validation(session):
    service = ControlService(session)
    policy = service.create_policy(_context("admin", roles=("project_admin",)), _policy_request())
    _artifact(session)

    with pytest.raises(ControlForbiddenError):
        service.propose(_context(project_id="other"), _propose_request(policy.id))
    with pytest.raises(ControlValidationError):
        service.propose(_context(), _propose_request(policy.id, parameters={"amount": 101}))


def test_simulation_provenance_validity_and_propose_idempotency(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    policy = service.create_policy(admin, _policy_request())
    _artifact(session)
    proposal = _propose_request(policy.id)
    command = service.propose(_context(), proposal)
    assert command.status == "pending_approval"
    assert command.parameter_digest and command.simulation_digest
    assert service.propose(_context(), proposal).id == command.id
    with pytest.raises(ControlIdempotencyConflict):
        service.propose(_context(), _propose_request(policy.id, parameters={"amount": 21}))


def test_single_and_dual_approval_are_distinct_and_expire(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    single = service.create_policy(admin, _policy_request())
    one = service.propose(_context(), _propose_request(single.id, idempotency_key="single"))
    approved = service.decide(one.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    assert approved.status == "approved"

    dual = service.create_policy(admin, _policy_request(approval_mode="dual", asset_type="gate"))
    two = service.propose(_context(), _propose_request(dual.id, idempotency_key="dual", target={"asset_id": "gate-1", "asset_type": "gate"}))
    first = service.decide(two.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    assert first.status == "pending_approval"
    with pytest.raises(ControlForbiddenError):
        service.decide(two.id, _context("reviewer", roles=("project_admin",)), "approve", "again")
    final = service.decide(two.id, _context("reviewer-2", roles=("project_admin",)), "approve", "ok")
    assert final.status == "approved"


def test_executor_fail_closed_replay_unknown_feedback_and_rollback(session):
    service = ControlService(session, executors={})
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    command = service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "request-1")

    executor = FakeExecutor({"outcome": "unknown", "external_id": "ext-unknown"})
    service.executors["reservoir"] = executor
    command = service.execute(command.id, admin, "request-1")
    assert command.status == "unknown"
    assert service.execute(command.id, admin, "request-1").id == command.id

    feedback = service.record_feedback(command.id, _context(), ControlFeedbackRequest(actual_state={"amount": 30}))
    assert feedback.status == "deviation"
    executor.result = {"outcome": "succeeded", "external_id": "ext-rb"}
    rolled = service.rollback(command.id, admin, "rollback-1")
    assert rolled.status == "rolled_back"


def test_permissions_require_authorization_grants_not_role_names(session):
    service = ControlService(session)
    context = _context("operator", roles=("user",))
    with pytest.raises(ControlForbiddenError):
        service.create_policy(context, _policy_request())


def test_execute_rejects_before_valid_from_and_target_asset_mismatch(session):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    service = ControlService(session, clock=lambda: now)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id, valid_from=now + timedelta(minutes=1), valid_until=now + timedelta(minutes=11)))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    assert service.execute(command.id, admin, "before-window").status == "expired"
    with pytest.raises(ControlValidationError):
        service.propose(_context(), _propose_request(policy.id, idempotency_key="bad-target", target={"asset_type": "gate"}))


def test_rollback_only_allows_terminal_failure_states(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    with pytest.raises(ControlExecutionError):
        service.rollback(command.id, admin, "rollback-before-approval")


def test_feedback_requires_timezone_and_valid_status(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    with pytest.raises(ControlValidationError):
        service.record_feedback(command.id, _context(), ControlFeedbackRequest(actual_state={"amount": 1}, observed_at=datetime.now()))


def test_target_asset_type_is_required_and_feedback_missing_expected_is_deviation(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    with pytest.raises(ControlValidationError):
        service.propose(_context(), _propose_request(policy.id, idempotency_key="missing-type", target={"asset_id": "r1"}))
    command = service.propose(_context(), _propose_request(policy.id, idempotency_key="missing-expected", expected_state={}))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    service.executors["reservoir"] = FakeExecutor()
    service.execute(command.id, admin, "exec")
    assert service.record_feedback(command.id, _context(), ControlFeedbackRequest(actual_state={})).status == "deviation"


def test_idempotency_digest_includes_immutable_request_fields(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    with pytest.raises(ControlIdempotencyConflict):
        service.propose(_context(), _propose_request(policy.id, target={"asset_id": "other", "asset_type": "reservoir"}))


def test_rollback_cannot_be_replayed_with_new_request_key(session):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve", "ok")
    service.executors["reservoir"] = FakeExecutor()
    command = service.execute(command.id, admin, "exec").model_copy()
    command = service.record_feedback(command.id, _context(), ControlFeedbackRequest(actual_state={"amount": 50}))
    service.rollback(command.id, admin, "rb1")
    with pytest.raises(ControlExecutionError):
        service.rollback(command.id, admin, "rb2")
