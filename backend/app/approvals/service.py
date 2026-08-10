import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.recorder import AuditRecordRequest, AuditRecorder
from app.conversations.models import AgentRun, ToolInvocation
from app.core.request_context import RequestContext

from .models import Approval, new_id


class ApprovalError(Exception):
    pass


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalForbiddenError(ApprovalError):
    pass


class ApprovalConflictError(ApprovalError):
    pass


def arguments_digest(arguments_summary: dict[str, Any]) -> str:
    payload = json.dumps(arguments_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(self, session: Session, clock: Callable[[], datetime] | None = None, audit_recorder: AuditRecorder | None = None):
        self.session = session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit_recorder = audit_recorder or AuditRecorder()

    def _audit(self, approval: Approval, context: RequestContext, action: str, status: str, *, reason: str | None = None) -> None:
        metadata = {"approval_id": approval.id, "tool_id": approval.tool_id}
        if reason:
            metadata["reason"] = reason
        self.audit_recorder.record(
            self.session,
            AuditRecordRequest(
                unit_id=approval.unit_id, project_id=approval.project_id, user_id=context.user_id,
                actor_roles=context.role_codes, authorization_scope="project", event_scope="project",
                category="security", source="system", action=action, status=status,
                risk_level=approval.risk_level, trace_id=approval.run_id, run_id=approval.run_id,
                resource_type="approval", resource_id=approval.id, resource_name=approval.tool_id,
                summary=reason or action, metadata=metadata, allowed_metadata_keys=frozenset(metadata),
                idempotency_key=f"approval:{approval.id}:{action}", occurred_at=self.clock(),
            ),
        )

    def create_request(
        self,
        *,
        run: AgentRun,
        invocation: ToolInvocation,
        context: RequestContext,
        risk_level: str = "high",
        reason: str | None = None,
        expires_at: datetime | None = None,
        assignee_role: str = "project_admin",
    ) -> Approval:
        existing = self.session.scalar(select(Approval).where(Approval.invocation_id == invocation.id))
        if existing is not None:
            raise ApprovalConflictError("approval already exists")
        approval = Approval(
            id=new_id(), run_id=run.id, invocation_id=invocation.id,
            tool_id=invocation.tool_id, tool_version=invocation.tool_version,
            unit_id=context.unit_id, project_id=context.project_id,
            requester_id=context.user_id, requester_roles=list(context.roles),
            assignee_role=assignee_role, risk_level=risk_level,
            arguments_summary=invocation.arguments_summary,
            arguments_digest=arguments_digest(invocation.arguments_summary),
            status="pending", reason=reason,
            expires_at=expires_at or (self.clock() + timedelta(hours=24)),
        )
        self.session.add(approval)
        self.session.flush()
        self._audit(approval, context, "approval.requested", "started", reason=reason)
        return approval

    def list_pending(self, context: RequestContext) -> list[Approval]:
        roles = set(context.roles)
        query = select(Approval).where(
            Approval.unit_id == context.unit_id,
            Approval.project_id == context.project_id,
            Approval.status == "pending",
            Approval.assignee_role.in_(roles),
        ).order_by(Approval.created_at, Approval.id)
        return list(self.session.scalars(query))

    def list_all(self, context: RequestContext, status: str) -> list[Approval]:
        query = select(Approval).where(
            Approval.unit_id == context.unit_id,
            Approval.project_id == context.project_id,
            or_(Approval.requester_id == context.user_id, Approval.assignee_role.in_(set(context.roles))),
        )
        if status != "all":
            query = query.where(Approval.status == status)
        return list(self.session.scalars(query.order_by(Approval.created_at.desc(), Approval.id.desc())))

    def get(self, approval_id: str, context: RequestContext) -> Approval:
        approval = self.session.scalar(select(Approval).where(
            Approval.id == approval_id,
            Approval.unit_id == context.unit_id,
            Approval.project_id == context.project_id,
        ))
        if approval is None:
            raise ApprovalNotFoundError(approval_id)
        if approval.requester_id != context.user_id and approval.assignee_role not in context.roles:
            raise ApprovalNotFoundError(approval_id)
        return approval

    def _check_decider(self, approval: Approval, context: RequestContext) -> None:
        if approval.assignee_role not in context.roles or context.user_id == approval.requester_id:
            raise ApprovalForbiddenError("approval permission denied")

    def _get_for_update(self, approval_id: str, context: RequestContext) -> Approval:
        approval = self.session.scalar(select(Approval).where(
            Approval.id == approval_id,
            Approval.unit_id == context.unit_id,
            Approval.project_id == context.project_id,
        ).with_for_update())
        if approval is None:
            raise ApprovalNotFoundError(approval_id)
        return approval

    def _check_pending(self, approval: Approval, invocation: ToolInvocation) -> None:
        if approval.status != "pending":
            raise ApprovalConflictError("approval is already decided")
        now = self.clock()
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            approval.status = "expired"
            self.session.flush()
            raise ApprovalConflictError("approval expired")
        if arguments_digest(invocation.arguments_summary) != approval.arguments_digest:
            raise ApprovalConflictError("approval arguments changed")

    def approve(self, approval_id: str, context: RequestContext, reason: str | None = None) -> Approval:
        approval = self._get_for_update(approval_id, context)
        self._check_decider(approval, context)
        invocation = self.session.get(ToolInvocation, approval.invocation_id)
        if invocation is None:
            raise ApprovalConflictError("tool invocation missing")
        self._check_pending(approval, invocation)
        approval.status = "approved"
        approval.decided_by = context.user_id
        approval.decision_reason = reason
        approval.decided_at = self.clock()
        self.session.flush()
        self._audit(approval, context, "approval.approved", "succeeded", reason=reason)
        return approval

    def prepare_execution(self, approval_id: str, context: RequestContext) -> tuple[Approval, ToolInvocation]:
        approval = self._get_for_update(approval_id, context)
        if approval.status != "approved":
            raise ApprovalConflictError("approval is not approved")
        invocation = self.session.get(ToolInvocation, approval.invocation_id)
        if invocation is None or invocation.status != "waiting_approval":
            raise ApprovalConflictError("tool invocation is not waiting for approval")
        if arguments_digest(invocation.arguments_summary) != approval.arguments_digest:
            raise ApprovalConflictError("approval arguments changed")
        return approval, invocation

    def reject(self, approval_id: str, context: RequestContext, reason: str | None = None) -> Approval:
        approval = self._get_for_update(approval_id, context)
        self._check_decider(approval, context)
        if approval.status != "pending":
            raise ApprovalConflictError("approval is already decided")
        approval.status = "rejected"
        approval.decided_by = context.user_id
        approval.decision_reason = reason
        approval.decided_at = self.clock()
        self.session.flush()
        self._audit(approval, context, "approval.rejected", "succeeded", reason=reason)
        return approval
