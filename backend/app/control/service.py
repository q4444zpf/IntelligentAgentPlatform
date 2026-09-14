from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.models import ArtifactRecord
from app.audit.recorder import AuditRecordRequest, AuditRecorder
from app.core.request_context import RequestContext

from .models import ControlCommand, ControlCommandApproval, ControlExecutionAttempt, ControlPolicy
from .schemas import ControlCommandInfo, ControlCommandProposeRequest, ControlFeedbackRequest, ControlPolicyCreateRequest, ControlPolicyInfo

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - minimal runtime fallback
    Draft202012Validator = None


class ControlError(ValueError):
    pass


class ControlForbiddenError(ControlError):
    pass


class ControlValidationError(ControlError):
    pass


class ControlIdempotencyConflict(ControlError):
    pass


class ControlExecutionError(ControlError):
    pass


@dataclass(frozen=True)
class ControlExecutorResult:
    outcome: str
    external_id: str | None = None
    summary: dict[str, Any] | None = None


class ControlExecutor(Protocol):
    def execute(self, command: ControlCommand, external_idempotency_key: str) -> ControlExecutorResult | dict[str, Any]: ...
    def rollback(self, command: ControlCommand, rollback_plan: dict[str, Any], external_idempotency_key: str) -> ControlExecutorResult | dict[str, Any]: ...


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


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
        permission = {"manage": "control.manage", "propose": "control.propose", "approve": "control.approve", "execute": "control.execute", "feedback": "control.feedback"}[action]
        authorization = context.authorization_context
        if authorization is None:
            raise ControlForbiddenError("control permission denied")
        from app.identity.authorization import AuthorizationService
        from app.identity.schemas import ResourceScope
        resource = ResourceScope(context.unit_id, context.project_id, context.user_id)
        if not AuthorizationService().allows(authorization, permission, resource):
            raise ControlForbiddenError("control permission denied")

    def _audit(self, context: RequestContext, action: str, resource_type: str, resource_id: str, status: str = "succeeded") -> None:
        self.audit.record(self.session, AuditRecordRequest(
            unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
            actor_roles=tuple(sorted(context.roles)), authorization_scope="project", event_scope="project",
            category="management", source="system", action=action, status=status, risk_level="high",
            idempotency_key=f"control:{resource_type}:{resource_id}:{action}:{context.user_id}:{uuid.uuid4()}", occurred_at=self.clock(),
            resource_type=resource_type, resource_id=resource_id, summary=action,
        ))

    def create_policy(self, context: RequestContext, request: ControlPolicyCreateRequest) -> ControlPolicyInfo:
        self._require(context, "manage")
        if request.parameter_schema.get("type") != "object":
            raise ControlValidationError("parameter_schema must describe an object")
        policy = ControlPolicy(unit_id=context.unit_id, project_id=context.project_id, owner_id=context.user_id, **request.model_dump())
        self.session.add(policy)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise ControlValidationError("policy already exists") from error
        self.session.refresh(policy)
        self._audit(context, "control_policy.created", "control_policy", policy.id)
        self.session.commit()
        return ControlPolicyInfo.model_validate(policy)

    def propose(self, context: RequestContext, request: ControlCommandProposeRequest) -> ControlCommandInfo:
        self._require(context, "propose")
        policy = self.session.scalar(select(ControlPolicy).where(ControlPolicy.id == request.policy_id, ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id))
        if policy is None or not policy.enabled:
            raise ControlForbiddenError("control policy not found")
        existing = self.session.scalar(select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id, ControlCommand.idempotency_key == request.idempotency_key))
        parameter_digest = _digest({"target": request.target, "parameters": request.parameters, "policy_id": request.policy_id, "simulation_artifact_id": request.simulation_artifact_id, "valid_from": request.valid_from.isoformat(), "valid_until": request.valid_until.isoformat(), "rollback_plan": request.rollback_plan, "expected_state": request.expected_state, "run_id": request.run_id, "team_version": request.team_version})
        if existing is not None:
            if existing.parameter_digest != parameter_digest or existing.policy_id != policy.id:
                raise ControlIdempotencyConflict("idempotency key is already used")
            return ControlCommandInfo.model_validate(existing)
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
        provenance = artifact.provenance if artifact is not None else {}
        simulation_kind = provenance.get("kind") or provenance.get("type") or provenance.get("artifact_type")
        simulation_status = provenance.get("status") or provenance.get("simulation_status")
        if artifact is None or simulation_status != "succeeded" or simulation_kind != "simulation":
            raise ControlValidationError("simulation artifact is not a successful simulation")
        if request.run_id is not None and artifact.run_id != request.run_id:
            raise ControlValidationError("simulation artifact run mismatch")
        target_type = request.target.get("asset_type")
        if target_type != policy.asset_type:
            raise ControlValidationError("target asset type does not match policy")
        command = ControlCommand(unit_id=context.unit_id, project_id=context.project_id, policy_id=policy.id, requester_id=context.user_id, run_id=request.run_id, team_version=request.team_version, target=request.target, parameters=request.parameters, parameter_digest=parameter_digest, simulation_artifact_id=artifact.id, simulation_digest=artifact.sha256, valid_from=valid_from, valid_until=valid_until, required_approvals=1 if policy.approval_mode == "single" else 2, rollback_plan=request.rollback_plan, expected_state=request.expected_state, idempotency_key=request.idempotency_key, status="pending_approval")
        self.session.add(command)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            existing = self.session.scalar(select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id, ControlCommand.idempotency_key == request.idempotency_key))
            if existing is not None:
                if existing.parameter_digest != parameter_digest or existing.policy_id != policy.id:
                    raise ControlIdempotencyConflict("idempotency key is already used") from error
                return ControlCommandInfo.model_validate(existing)
            raise ControlIdempotencyConflict("idempotency key is already used") from error
        self.session.refresh(command)
        self._audit(context, "control_command.proposed", "control_command", command.id)
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    def _command(self, command_id: str, context: RequestContext) -> ControlCommand:
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id))
        if command is None:
            raise ControlForbiddenError("control command not found")
        return command

    def decide(self, command_id: str, context: RequestContext, decision: str, reason: str | None = None) -> ControlCommandInfo:
        self._require(context, "approve")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update())
        if command is None:
            raise ControlForbiddenError("control command not found")
        if context.user_id == command.requester_id:
            raise ControlForbiddenError("requester cannot approve own command")
        now = self.clock()
        if _stored_aware(command.valid_until) <= _stored_aware(now) and command.status == "pending_approval":
            command.status = "expired"
            self.session.commit()
            raise ControlValidationError("command expired")
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
        self.session.commit()
        self._audit(context, f"control_command.{ 'approved' if decision == 'approve' else 'rejected' }", "control_command", command.id)
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    @staticmethod
    def _result(value: ControlExecutorResult | dict[str, Any]) -> ControlExecutorResult:
        return value if isinstance(value, ControlExecutorResult) else ControlExecutorResult(outcome=value.get("outcome", "failed"), external_id=value.get("external_id"), summary=value.get("summary") or value)

    def execute(self, command_id: str, context: RequestContext, request_key: str) -> ControlCommandInfo:
        self._require(context, "execute")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update())
        if command is None:
            raise ControlForbiddenError("control command not found")
        existing = self.session.scalar(select(ControlExecutionAttempt).where(ControlExecutionAttempt.command_id == command.id, ControlExecutionAttempt.request_key == request_key))
        if existing is not None:
            return ControlCommandInfo.model_validate(command)
        now = _stored_aware(self.clock())
        if now < _stored_aware(command.valid_from) or now >= _stored_aware(command.valid_until):
            command.status = "expired"
            self.session.commit()
            return ControlCommandInfo.model_validate(command)
        if command.status != "approved":
            raise ControlExecutionError("command is not approved")
        executor = self.executors.get(self.session.get(ControlPolicy, command.policy_id).asset_type)
        if executor is None:
            raise ControlExecutionError("no executor registered")
        command.status = "executing"
        external_key = f"control:{command.id}:execute"
        self.session.commit()
        try:
            result = self._result(executor.execute(command, external_key))
        except Exception as error:
            command.status = "unknown"
            self.session.add(ControlExecutionAttempt(command_id=command.id, kind="execute", request_key=request_key, external_idempotency_key=external_key, outcome="unknown", result_summary={"error": "executor failure"}))
            self.session.commit()
            raise ControlExecutionError("executor failed") from error
        command.external_command_ref = result.external_id
        command.status = {"succeeded": "succeeded", "unknown": "unknown"}.get(result.outcome, "failed")
        self.session.add(ControlExecutionAttempt(command_id=command.id, kind="execute", request_key=request_key, external_idempotency_key=external_key, outcome=result.outcome, result_summary=result.summary or {}))
        self.session.commit()
        self._audit(context, "control_command.executed", "control_command", command.id)
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    def record_feedback(self, command_id: str, context: RequestContext, request: ControlFeedbackRequest) -> ControlCommandInfo:
        self._require(context, "feedback")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update())
        if command is None:
            raise ControlForbiddenError("control command not found")
        if request.observed_at is not None:
            _aware(request.observed_at)
        if command.status not in {"unknown", "executing", "succeeded", "deviation", "rollback_pending"}:
            raise ControlValidationError("feedback is not allowed for command state")
        policy = self.session.get(ControlPolicy, command.policy_id)
        command.actual_state = request.actual_state
        deviations = {}
        for key, threshold in (policy.deviation_thresholds or {}).items():
            expected = (command.expected_state or {}).get(key)
            actual = request.actual_state.get(key)
            if expected is None or actual is None:
                deviations[key] = {"expected": expected, "actual": actual, "threshold": threshold}
            elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and abs(actual - expected) > float(threshold):
                deviations[key] = {"expected": expected, "actual": actual, "threshold": threshold}
        command.deviation_result = {"deviations": deviations, "within_threshold": not deviations}
        if command.status in {"unknown", "succeeded", "executing"}:
            command.status = "deviation" if deviations else "succeeded"
        self.session.commit()
        self._audit(context, "control_command.feedback", "control_command", command.id)
        self.session.commit()
        return ControlCommandInfo.model_validate(command)

    def rollback(self, command_id: str, context: RequestContext, request_key: str) -> ControlCommandInfo:
        self._require(context, "execute")
        command = self.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).with_for_update())
        if command is None:
            raise ControlForbiddenError("control command not found")
        if command.status not in {"failed", "unknown", "deviation", "rollback_pending"}:
            raise ControlExecutionError("rollback is not allowed for command state")
        existing = self.session.scalar(select(ControlExecutionAttempt).where(ControlExecutionAttempt.command_id == command.id, ControlExecutionAttempt.request_key == request_key))
        if existing is not None:
            return ControlCommandInfo.model_validate(command)
        policy = self.session.get(ControlPolicy, command.policy_id)
        executor = self.executors.get(policy.asset_type)
        if executor is None:
            command.status = "rollback_pending"
            self.session.commit()
            raise ControlExecutionError("no executor registered")
        command.status = "rollback_pending"
        external_key = f"control:{command.id}:rollback"
        try:
            result = self._result(executor.rollback(command, command.rollback_plan, external_key))
        except Exception as error:
            command.status = "rollback_pending"
            self.session.add(ControlExecutionAttempt(command_id=command.id, kind="rollback", request_key=request_key, external_idempotency_key=external_key, outcome="unknown", result_summary={"error": "executor failure"}))
            self.session.commit()
            raise ControlExecutionError("rollback executor failed") from error
        command.status = "rolled_back" if result.outcome == "succeeded" else ("unknown" if result.outcome == "unknown" else "rollback_pending")
        self.session.add(ControlExecutionAttempt(command_id=command.id, kind="rollback", request_key=request_key, external_idempotency_key=external_key, outcome=result.outcome, result_summary=result.summary or {}))
        self.session.commit()
        self._audit(context, "control_command.rolled_back", "control_command", command.id)
        self.session.commit()
        return ControlCommandInfo.model_validate(command)
