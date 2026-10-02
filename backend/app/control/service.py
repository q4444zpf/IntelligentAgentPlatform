from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.artifacts.models import ArtifactRecord
from app.conversations.models import AgentRun, Conversation
from app.collaboration.models import Team, TeamVersion
from app.runtime.runner_gateway_schemas import ArtifactProvenance, SimulationAttestation
from app.audit.recorder import AuditRecordRequest, AuditRecorder
from app.core.request_context import RequestContext

from .errors import (ControlCommandExpired, ControlExecutionError, ControlExecutionInProgress, ControlExecutorUnavailable,
                     ControlForbiddenError, ControlIdempotencyConflict, ControlNotFoundError, ControlValidationError)
from .models import ControlCommand, ControlCommandApproval, ControlExecutionAttempt, ControlFeedbackRecord, ControlPolicy, new_id
from .schemas import (ControlCommandDetail, ControlCommandInfo, ControlCommandListItem, ControlCommandProposeRequest,
                      ControlExecutionOutcome, ControlFeedbackRequest, ControlPolicyCreateRequest, ControlPolicyInfo,
                      ControlPolicyUpdateRequest)

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - minimal runtime fallback
    Draft202012Validator = None


class ControlExecutorResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    outcome: ControlExecutionOutcome
    external_id: str | None = Field(default=None, max_length=160)
    summary: dict[str, Any] | None = None


class ControlExecutor(Protocol):
    def execute(self, command: ControlCommand, external_idempotency_key: str) -> ControlExecutorResult | dict[str, Any]: ...
    def rollback(self, command: ControlCommand, rollback_plan: dict[str, Any], external_idempotency_key: str) -> ControlExecutorResult | dict[str, Any]: ...


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _is_domain_unique_violation(error: IntegrityError, constraint: str, columns: tuple[str, ...]) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == constraint:
        return True
    return str(error.orig).casefold() == f"unique constraint failed: {', '.join(columns)}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ControlValidationError("datetime must include timezone information")
    return value.astimezone(timezone.utc)


def _stored_aware(value: datetime) -> datetime:
    """SQLite returns timezone columns as naive; persisted values are UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _schema_validate(schema: dict[str, Any], value: Any, path: str = "parameters") -> None:
    typ = schema.get("type")
    if typ == "object":
        if not isinstance(value, dict):
            raise ControlValidationError(f"{path} must be an object")
        for key in schema.get("required", []):
            if key not in value:
                raise ControlValidationError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(schema.get("properties", {}))
            if unknown:
                raise ControlValidationError(f"{path} contains unknown properties")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _schema_validate(child, value[key], f"{path}.{key}")
    elif typ == "array":
        if not isinstance(value, list):
            raise ControlValidationError(f"{path} must be an array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ControlValidationError(f"{path} has too few items")
        for i, item in enumerate(value):
            _schema_validate(schema.get("items", {}), item, f"{path}[{i}]")
    elif typ == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ControlValidationError(f"{path} must be a number")
    elif typ == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ControlValidationError(f"{path} must be an integer")
    elif typ == "string" and not isinstance(value, str):
        raise ControlValidationError(f"{path} must be a string")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ControlValidationError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ControlValidationError(f"{path} is above maximum")
    if "enum" in schema and value not in schema["enum"]:
        raise ControlValidationError(f"{path} has an invalid value")


class ControlService:
    def __init__(self, session: Session, *, executors: dict[str, ControlExecutor] | None = None, clock=None):
        self.session = session
        self.executors = executors or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = AuditRecorder()

    @staticmethod
    def _is_admin(context: RequestContext) -> bool:
        return bool(set(context.roles) & {"unit_admin", "project_admin"})

    def _require(self, context: RequestContext, action: str) -> None:
        permission = {"read": "control.read", "manage": "control.manage", "propose": "control.propose", "approve": "control.approve", "execute": "control.execute", "feedback": "control.feedback"}[action]
        authorization = context.authorization_context
        if authorization is None:
            raise ControlForbiddenError("control permission denied")
        from app.identity.authorization import AuthorizationService
        from app.identity.schemas import ResourceScope
        resource = ResourceScope(context.unit_id, context.project_id, context.user_id)
        if not AuthorizationService().allows(authorization, permission, resource):
            raise ControlForbiddenError("control permission denied")

    def _audit(self, context: RequestContext, action: str, resource_type: str, resource_id: str, operation_id: str, status: str = "succeeded") -> None:
        self.audit.record(self.session, AuditRecordRequest(
            unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
            actor_roles=tuple(sorted(context.roles)), authorization_scope="project", event_scope="project",
            category="management", source="system", action=action, status=status, risk_level="high",
            idempotency_key=f"control:{resource_id}:{operation_id}", occurred_at=self.clock(),
            resource_type=resource_type, resource_id=resource_id, summary=action,
        ))

    def create_policy(self, context: RequestContext, request: ControlPolicyCreateRequest) -> ControlPolicyInfo:
        self._require(context, "manage")
        self._check_policy_schema(request.parameter_schema)
        self._check_thresholds(request.deviation_thresholds)
        policy = ControlPolicy(unit_id=context.unit_id, project_id=context.project_id, owner_id=context.user_id, **request.model_dump())
        policy.id = new_id()
        self.session.add(policy)
        try:
            self._audit(context, "control_policy.created", "control_policy", policy.id, "created")
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            if not _is_domain_unique_violation(error, "uq_control_policy_pair", (
                "control_policies.unit_id", "control_policies.project_id",
                "control_policies.asset_type", "control_policies.command_type",
            )):
                raise
            raise ControlValidationError("policy already exists") from error
        self.session.refresh(policy)
        return ControlPolicyInfo.model_validate(policy)

    @staticmethod
    def _check_thresholds(thresholds: Any) -> None:
        if not isinstance(thresholds, dict) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or value < 0 or (isinstance(value, float) and not math.isfinite(value))
            for value in thresholds.values()
        ):
            raise ControlValidationError("deviation thresholds must be finite nonnegative numbers")

    @staticmethod
    def _check_policy_schema(schema: dict[str, Any]) -> None:
        if schema.get("type") != "object":
            raise ControlValidationError("parameter_schema must describe an object")
        if Draft202012Validator is not None:
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as error:
                raise ControlValidationError("invalid parameter schema") from error

    def get_policy(self, context: RequestContext, policy_id: str) -> ControlPolicyInfo:
        self._require(context, "read")
        policy = self.session.scalar(select(ControlPolicy).where(ControlPolicy.id == policy_id, ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id))
        if policy is None:
            raise ControlNotFoundError()
        return ControlPolicyInfo.model_validate(policy)

    def list_policies(self, context: RequestContext, *, enabled: bool | None = None, asset_type: str | None = None, command_type: str | None = None) -> list[ControlPolicyInfo]:
        self._require(context, "read")
        query = select(ControlPolicy).where(ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id)
        if enabled is not None:
            query = query.where(ControlPolicy.enabled == enabled)
        if asset_type is not None:
            query = query.where(ControlPolicy.asset_type == asset_type)
        if command_type is not None:
            query = query.where(ControlPolicy.command_type == command_type)
        return [ControlPolicyInfo.model_validate(row) for row in self.session.scalars(query.order_by(ControlPolicy.created_at, ControlPolicy.id))]

    def update_policy(self, context: RequestContext, policy_id: str, request: ControlPolicyUpdateRequest) -> ControlPolicyInfo:
        self._require(context, "manage")
        policy = self.session.scalar(select(ControlPolicy).where(ControlPolicy.id == policy_id, ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id).with_for_update())
        if policy is None:
            raise ControlNotFoundError()
        changes = request.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise ControlValidationError("policy fields cannot be null")
        if "parameter_schema" in changes:
            self._check_policy_schema(changes["parameter_schema"])
        self._check_thresholds(changes.get("deviation_thresholds", policy.deviation_thresholds))
        if changes and any(getattr(policy, key) != value for key, value in changes.items()):
            for key, value in changes.items():
                setattr(policy, key, value)
            policy.revision += 1
            self._audit(context, "control_policy.updated", "control_policy", policy.id, f"update:{policy.revision}")
            self.session.commit()
        return ControlPolicyInfo.model_validate(policy)

    def propose(self, context: RequestContext, request: ControlCommandProposeRequest) -> ControlCommandInfo:
        self._require(context, "propose")
        policy = self.session.scalar(select(ControlPolicy).where(ControlPolicy.id == request.policy_id, ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id))
        if policy is None or not policy.enabled:
            raise ControlNotFoundError()
        existing = self.session.scalar(select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id, ControlCommand.idempotency_key == request.idempotency_key))
        parameter_digest = _digest({"target": request.target, "parameters": request.parameters, "policy_id": request.policy_id, "simulation_artifact_id": request.simulation_artifact_id, "valid_from": request.valid_from.isoformat(), "valid_until": request.valid_until.isoformat(), "rollback_plan": request.rollback_plan, "expected_state": request.expected_state, "run_id": request.run_id, "team_version_id": request.team_version_id})
        if existing is not None:
            if existing.parameter_digest != parameter_digest or existing.policy_id != policy.id:
                raise ControlIdempotencyConflict("idempotency key is already used")
            return ControlCommandInfo.model_validate(existing)
        self._check_thresholds(policy.deviation_thresholds)
        if Draft202012Validator is not None:
            try:
                validator = Draft202012Validator(policy.parameter_schema)
                errors = list(validator.iter_errors(request.parameters))
            except Exception as error:
                raise ControlValidationError("invalid parameter schema") from error
            if errors:
                raise ControlValidationError("parameters do not match policy schema")
        else:
            _schema_validate(policy.parameter_schema, request.parameters)
        valid_from, valid_until = _aware(request.valid_from), _aware(request.valid_until)
        if valid_until <= valid_from or (valid_until - valid_from).total_seconds() > policy.max_command_lifetime_seconds:
            raise ControlValidationError("validity window exceeds policy")
        artifact = self.session.scalar(select(ArtifactRecord).where(ArtifactRecord.id == request.simulation_artifact_id, ArtifactRecord.unit_id == context.unit_id, ArtifactRecord.project_id == context.project_id, ArtifactRecord.status == "active"))
        if artifact is None or artifact.run_id is None:
            raise ControlValidationError("simulation artifact is not a successful simulation")
        try:
            provenance = ArtifactProvenance.model_validate(artifact.provenance)
            attestation = SimulationAttestation.model_validate(provenance.simulation_attestation)
        except ValidationError as error:
            raise ControlValidationError("simulation artifact is not a successful simulation") from error
        if artifact.sha256 != attestation.artifact_sha256:
            raise ControlValidationError("simulation artifact digest mismatch")
        self._validated_run_binding(context, request.run_id, request.team_version_id, artifact)
        target_type = request.target.get("asset_type")
        if target_type != policy.asset_type:
            raise ControlValidationError("target asset type does not match policy")
        policy_snapshot = {key: getattr(policy, key) for key in ControlPolicyCreateRequest.model_fields}
        command = ControlCommand(id=new_id(), unit_id=context.unit_id, project_id=context.project_id, policy_id=policy.id, requester_id=context.user_id, run_id=request.run_id, team_version_id=request.team_version_id, policy_digest=_digest(policy_snapshot), policy_snapshot=deepcopy(policy_snapshot), deviation_thresholds_snapshot=deepcopy(policy.deviation_thresholds), target=request.target, parameters=request.parameters, parameter_digest=parameter_digest, simulation_artifact_id=artifact.id, simulation_digest=artifact.sha256, valid_from=valid_from, valid_until=valid_until, required_approvals=1 if policy.approval_mode == "single" else 2, rollback_plan=request.rollback_plan, expected_state=request.expected_state, idempotency_key=request.idempotency_key, status="pending_approval")
        self.session.add(command)
        try:
            self._audit(context, "control_command.proposed", "control_command", command.id, "propose")
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            if not _is_domain_unique_violation(error, "uq_control_command_idempotency", (
                "control_commands.unit_id", "control_commands.project_id", "control_commands.idempotency_key",
            )):
                raise
            existing = self.session.scalar(select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id, ControlCommand.idempotency_key == request.idempotency_key))
            if existing is not None:
                if existing.parameter_digest != parameter_digest or existing.policy_id != policy.id:
                    raise ControlIdempotencyConflict("idempotency key is already used") from error
                return ControlCommandInfo.model_validate(existing)
            raise ControlIdempotencyConflict("idempotency key is already used") from error
        self.session.refresh(command)
        return ControlCommandInfo.model_validate(command)

    def _validated_run_binding(
        self, context: RequestContext, run_id: str | None,
        team_version_id: str | None, artifact: ArtifactRecord,
    ) -> AgentRun | None:
        if (run_id is None) != (team_version_id is None):
            raise ControlValidationError("run_id and team_version_id must be supplied together")
        try:
            provenance = ArtifactProvenance.model_validate(artifact.provenance)
        except ValidationError as error:
            raise ControlValidationError("simulation artifact provenance invalid") from error
        bound_run_id = run_id or artifact.run_id
        bound_version_id = team_version_id or provenance.team_version_id
        run = self.session.scalar(select(AgentRun).join(Conversation, Conversation.id == AgentRun.conversation_id).where(
            AgentRun.id == bound_run_id, Conversation.unit_id == context.unit_id,
            Conversation.project_id == context.project_id,
        ))
        version = self.session.scalar(select(TeamVersion).join(Team, Team.id == TeamVersion.team_id).where(
            TeamVersion.id == bound_version_id, TeamVersion.status == "published",
            Team.unit_id == context.unit_id, Team.project_id == context.project_id,
        ))
        if (run is None or version is None or run.actor_type != "team"
                or run.actor_id != version.team_id or run.actor_version_id != bound_version_id
                or artifact.run_id != bound_run_id or provenance.team_version_id != bound_version_id):
            raise ControlValidationError("simulation artifact published Team Run binding mismatch")
        return run

    def _command(self, command_id: str, context: RequestContext) -> ControlCommand:
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id))
        if command is None:
            raise ControlNotFoundError()
        return command

    def _approval_count(self, command_id: str) -> int:
        return self.session.scalar(select(func.count(ControlCommandApproval.id)).where(
            ControlCommandApproval.command_id == command_id, ControlCommandApproval.decision == "approve",
        )) or 0

    def _recovery_operation(self, command: ControlCommand) -> dict[str, str] | None:
        attempts = self.session.scalars(select(ControlExecutionAttempt).where(
            ControlExecutionAttempt.command_id == command.id,
        )).all()
        rollback = next((attempt for attempt in attempts if attempt.kind == "rollback"), None)
        attempt = rollback or next((attempt for attempt in attempts if attempt.kind == "execute"), None)
        if attempt is None or attempt.completed_at is not None:
            return None
        states = {"rollback_pending", "unknown"} if attempt.kind == "rollback" else {"executing", "unknown"}
        if command.status not in states:
            return None
        return {"kind": attempt.kind, "request_key": attempt.request_key}

    def list_commands(self, context: RequestContext, *, status: str | None = None, policy_id: str | None = None,
                      created_after: datetime | None = None, created_before: datetime | None = None) -> list[ControlCommandListItem]:
        self._require(context, "read")
        query = select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id)
        if status is not None:
            query = query.where(ControlCommand.status == status)
        if policy_id is not None:
            query = query.where(ControlCommand.policy_id == policy_id)
        if created_after is not None:
            query = query.where(ControlCommand.created_at >= _aware(created_after))
        if created_before is not None:
            query = query.where(ControlCommand.created_at <= _aware(created_before))
        return [ControlCommandListItem(**ControlCommandInfo.model_validate(row).model_dump(), approval_count=self._approval_count(row.id), recovery_operation=self._recovery_operation(row))
                for row in self.session.scalars(query.order_by(ControlCommand.created_at, ControlCommand.id))]

    def get_command_detail(self, context: RequestContext, command_id: str) -> ControlCommandDetail:
        self._require(context, "read")
        command = self._command(command_id, context)
        approvals = self.session.scalars(select(ControlCommandApproval).where(ControlCommandApproval.command_id == command.id).order_by(ControlCommandApproval.decided_at, ControlCommandApproval.id)).all()
        attempts = self.session.scalars(select(ControlExecutionAttempt).where(ControlExecutionAttempt.command_id == command.id).order_by(ControlExecutionAttempt.created_at, ControlExecutionAttempt.id)).all()
        feedback = self.session.scalars(select(ControlFeedbackRecord).where(ControlFeedbackRecord.command_id == command.id).order_by(ControlFeedbackRecord.observed_at, ControlFeedbackRecord.id)).all()
        artifact = self.session.get(ArtifactRecord, command.simulation_artifact_id)
        attestation = (artifact.provenance or {}).get("simulation_attestation", {}) if artifact is not None else {}
        rollback = next(({"attempt_id": attempt.id, "outcome": attempt.outcome, "external_id": attempt.external_id}
                         for attempt in attempts if attempt.kind == "rollback"), None)
        return ControlCommandDetail(
            **ControlCommandInfo.model_validate(command).model_dump(), approval_count=self._approval_count(command.id),
            recovery_operation=self._recovery_operation(command),
            approval_decisions=[{"approver_id": row.approver_id, "decision": row.decision, "reason": row.reason, "decided_at": row.decided_at} for row in approvals],
            attempts=[{"id": row.id, "kind": row.kind, "outcome": row.outcome, "external_id": row.external_id, "created_at": row.created_at, "completed_at": row.completed_at} for row in attempts],
            feedback_records=[{"id": row.id, "recorder_id": row.recorder_id, "observed_at": row.observed_at, "actual_state": row.actual_state, "deviation_result": row.deviation_result} for row in feedback],
            simulation_attestation=attestation, rollback_provenance=rollback,
        )

    def decide(self, command_id: str, context: RequestContext, decision: str, reason: str | None = None) -> ControlCommandInfo:
        self._require(context, "approve")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update())
        if command is None:
            raise ControlNotFoundError()
        if context.user_id == command.requester_id:
            raise ControlForbiddenError("requester cannot approve own command")
        now = self.clock()
        if _stored_aware(command.valid_until) <= _stored_aware(now) and command.status == "pending_approval":
            command.status = "expired"
            self._audit(context, "control_command.expired", "control_command", command.id, "expired")
            self.session.commit()
            raise ControlCommandExpired()
        if command.status != "pending_approval" or decision not in {"approve", "reject"}:
            raise ControlValidationError("command is not awaiting approval")
        if self.session.scalar(select(ControlCommandApproval).where(ControlCommandApproval.command_id == command.id, ControlCommandApproval.approver_id == context.user_id).with_for_update()):
            raise ControlForbiddenError("approver already decided")
        self.session.add(ControlCommandApproval(command_id=command.id, approver_id=context.user_id, decision=decision, reason=reason, decided_at=now))
        if decision == "reject":
            command.status = "rejected"
        else:
            count = self.session.scalar(select(func.count(ControlCommandApproval.id)).where(ControlCommandApproval.command_id == command.id, ControlCommandApproval.decision == "approve"))
            if (count or 0) >= command.required_approvals:
                command.status = "approved"
        self._audit(context, f"control_command.{ 'approved' if decision == 'approve' else 'rejected' }", "control_command", command.id, f"{decision}:{context.user_id}")
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    @staticmethod
    def _result(value: ControlExecutorResult | dict[str, Any]) -> ControlExecutorResult:
        result = ControlExecutorResult.model_validate(value)
        json.dumps(result.summary, allow_nan=False)
        return result

    def _begin_attempt(self, command: ControlCommand, kind: str, request_key: str) -> ControlExecutionAttempt:
        """Stage an operation while the caller holds the command lock."""
        existing = self.session.scalar(select(ControlExecutionAttempt).where(
            ControlExecutionAttempt.command_id == command.id,
            ControlExecutionAttempt.kind == kind,
        ).execution_options(populate_existing=True))
        if existing is not None:
            if existing.request_key == request_key:
                return existing
            if existing.completed_at is None:
                raise ControlExecutionInProgress()
            raise ControlExecutionError("control operation already attempted")
        allowed = {"approved"} if kind == "execute" else {"failed", "unknown", "deviation", "rollback_pending"}
        if command.status not in allowed:
            raise ControlExecutionError("command is not approved" if kind == "execute" else "rollback is not allowed for command state")
        attempt = ControlExecutionAttempt(
            id=new_id(),
            command_id=command.id, kind=kind, request_key=request_key,
            external_idempotency_key=f"control:{command.id}:{kind}",
            outcome="pending", result_summary={}, created_at=self.clock(),
        )
        self.session.add(attempt)
        command.status = "executing" if kind == "execute" else "rollback_pending"
        self.session.flush()
        return attempt

    def _complete_attempt(self, command_id: str, attempt_id: str, result: ControlExecutorResult | None) -> ControlCommandInfo:
        """Stage the result; a missing response leaves reconciliation open."""
        command = self.session.scalar(select(ControlCommand).where(
            ControlCommand.id == command_id,
        ).with_for_update().execution_options(populate_existing=True))
        attempt = self.session.scalar(select(ControlExecutionAttempt).where(
            ControlExecutionAttempt.id == attempt_id,
            ControlExecutionAttempt.command_id == command_id,
        ).with_for_update().execution_options(populate_existing=True))
        if command is None or attempt is None:
            raise ControlExecutionError("control execution attempt not found")
        if attempt.completed_at is not None:
            return ControlCommandInfo.model_validate(command)
        attempt.outcome = result.outcome if result is not None else "unknown"
        attempt.external_id = result.external_id if result is not None else None
        attempt.result_summary = (result.summary or {}) if result is not None else {"error": "executor response unavailable or invalid"}
        attempt.completed_at = self.clock() if result is not None else None
        if attempt.kind == "execute":
            # A late execute response must not overwrite a physical rollback.
            rollback_started = self.session.scalar(select(ControlExecutionAttempt.id).where(
                ControlExecutionAttempt.command_id == command.id, ControlExecutionAttempt.kind == "rollback",
            ))
            if command.status in {"executing", "unknown"} and rollback_started is None:
                command.external_command_ref = attempt.external_id
                command.status = "awaiting_feedback" if attempt.outcome == "succeeded" else attempt.outcome
        else:
            command.status = {"succeeded": "rolled_back", "unknown": "unknown"}.get(attempt.outcome, "rollback_pending")
        return ControlCommandInfo.model_validate(command)

    def _run_attempt(self, command_id: str, context: RequestContext, request_key: str, kind: str) -> ControlCommandInfo:
        self._require(context, "execute")
        command = self.session.scalar(select(ControlCommand).where(
            ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id,
            ControlCommand.project_id == context.project_id,
        ).with_for_update().execution_options(populate_existing=True))
        if command is None:
            raise ControlNotFoundError()
        existing = self.session.scalar(select(ControlExecutionAttempt).where(
            ControlExecutionAttempt.command_id == command.id,
            ControlExecutionAttempt.kind == kind,
        ))
        if existing is not None:
            attempt = self._begin_attempt(command, kind, request_key)
            if attempt.completed_at is not None:
                return ControlCommandInfo.model_validate(command)
            if kind == "execute":
                now = _stored_aware(self.clock())
                if now < _stored_aware(command.valid_from):
                    raise ControlExecutionError("command validity window has not started")
                if now >= _stored_aware(command.valid_until):
                    raise ControlCommandExpired()
            if kind == "execute" and self.session.scalar(select(ControlExecutionAttempt.id).where(
                ControlExecutionAttempt.command_id == command.id, ControlExecutionAttempt.kind == "rollback",
            )):
                raise ControlExecutionError("execute reconciliation is not allowed after rollback started")
        elif kind == "execute":
            now = _stored_aware(self.clock())
            if now < _stored_aware(command.valid_from):
                raise ControlExecutionError("command validity window has not started")
            if now >= _stored_aware(command.valid_until):
                command.status = "expired"
                self._audit(context, "control_command.expired", "control_command", command.id, "expired")
                self.session.commit()
                raise ControlCommandExpired()
        elif command.status not in {"failed", "unknown", "deviation", "rollback_pending"}:
            raise ControlExecutionError("rollback is not allowed for command state")
        if kind == "execute":
            self._check_thresholds(command.deviation_thresholds_snapshot)
        executor = self.executors.get(self.session.get(ControlPolicy, command.policy_id).asset_type)
        if executor is None:
            raise ControlExecutorUnavailable()
        attempt = self._begin_attempt(command, kind, request_key)
        attempt_id, external_key = attempt.id, attempt.external_idempotency_key
        self._audit(context, f"control_command.{kind}_started", "control_command", command.id, f"{kind}:{attempt_id}:started", status="started")
        self.session.commit()
        transport_error = None
        try:
            raw_result = executor.execute(command, external_key) if kind == "execute" else executor.rollback(command, command.rollback_plan, external_key)
        except Exception as error:
            transport_error = error
            result = None
        else:
            try:
                result = self._result(raw_result)
            except (ValueError, TypeError):
                result = None
        self._complete_attempt(command_id, attempt_id, result)
        completed_action = f"control_command.{kind}_completed" if result is not None and result.outcome != "unknown" else f"control_command.{kind}_unknown"
        transition = "completed" if result is not None and result.outcome != "unknown" else "unknown"
        self._audit(context, completed_action, "control_command", command_id, f"{kind}:{attempt_id}:{transition}",
                    status="succeeded" if result is not None and result.outcome == "succeeded" else "failed")
        self.session.commit()
        if transport_error is not None:
            raise ControlExecutorUnavailable() from transport_error
        return ControlCommandInfo.model_validate(command)

    def execute(self, command_id: str, context: RequestContext, request_key: str) -> ControlCommandInfo:
        return self._run_attempt(command_id, context, request_key, "execute")

    def record_feedback(self, command_id: str, context: RequestContext, request: ControlFeedbackRequest) -> ControlCommandInfo:
        self._require(context, "feedback")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update().execution_options(populate_existing=True))
        if command is None:
            raise ControlNotFoundError()
        now = _stored_aware(self.clock())
        observed_at = _aware(request.observed_at) if request.observed_at is not None else now
        if command.status not in {"awaiting_feedback", "unknown", "succeeded", "deviation", "rollback_pending"}:
            raise ControlValidationError("feedback is not allowed for command state")
        attempt = self.session.scalar(select(ControlExecutionAttempt).where(
            ControlExecutionAttempt.command_id == command.id, ControlExecutionAttempt.kind == "execute",
        ).execution_options(populate_existing=True))
        if attempt is None:
            raise ControlValidationError("feedback requires an execution attempt")
        lower_bound = attempt.completed_at or attempt.created_at
        if observed_at < _stored_aware(lower_bound) or observed_at > now + timedelta(minutes=5):
            raise ControlValidationError("feedback observation is stale or in the future")
        if command.feedback_observed_at is not None and observed_at <= _stored_aware(command.feedback_observed_at):
            raise ControlValidationError("feedback observations must increase monotonically")
        self._check_thresholds(command.deviation_thresholds_snapshot)
        command.actual_state = request.actual_state
        deviations = {}
        for key, threshold in command.deviation_thresholds_snapshot.items():
            expected = (command.expected_state or {}).get(key)
            actual = request.actual_state.get(key)
            numeric = all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (expected, actual))
            if not numeric or not abs(actual - expected) <= threshold:
                deviations[key] = {"expected": expected, "actual": actual, "threshold": threshold}
        command.deviation_result = {"deviations": deviations, "within_threshold": not deviations}
        command.feedback_observed_at = observed_at
        feedback = ControlFeedbackRecord(id=new_id(),
            command_id=command.id, recorder_id=context.user_id, observed_at=observed_at,
            actual_state=deepcopy(request.actual_state), deviation_result=deepcopy(command.deviation_result),
        )
        self.session.add(feedback)
        if command.status in {"awaiting_feedback", "succeeded", "deviation"}:
            command.status = "deviation" if deviations else "succeeded"
        self._audit(context, "control_command.feedback", "control_command", command.id, f"feedback:{feedback.id}")
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    def rollback(self, command_id: str, context: RequestContext, request_key: str) -> ControlCommandInfo:
        return self._run_attempt(command_id, context, request_key, "rollback")
