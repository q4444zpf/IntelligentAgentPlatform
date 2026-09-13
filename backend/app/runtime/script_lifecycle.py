from datetime import UTC, datetime

from sqlalchemy import update

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.models import ToolInvocation
from app.conversations.repository import ConversationRepository


def finish_script_invocation(
    repository: ConversationRepository,
    audit_recorder: AuditRecorder,
    invocation: ToolInvocation,
    *,
    status: str,
    error_code: str | None,
    duration_ms: int,
) -> bool:
    """Finish once in the caller's transaction, after acquiring the run lock."""
    session = repository.session
    transition = session.execute(
        update(ToolInvocation)
        .where(ToolInvocation.id == invocation.id, ToolInvocation.status == "running")
        .values(
            status=status,
            error_code=error_code,
            duration_ms=duration_ms,
            completed_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    session.refresh(invocation)
    if transition.rowcount == 0:
        return False
    metadata = {
        "lease_id": invocation.id,
        "script_name": invocation.tool_id,
        "duration_ms": duration_ms,
    }
    repository.append_event(
        invocation.run_id,
        f"skill.script.{status}",
        {**metadata, **({"error_code": error_code} if error_code else {})},
    )
    context = repository.get_run_execution_context(invocation.run_id)
    if context is not None:
        audit_recorder.record(session, AuditRecordRequest(
            unit_id=str(context["unit_id"]),
            project_id=str(context["project_id"]),
            user_id=str(context["user_id"]),
            actor_roles=tuple(context["actor_roles"]),
            authorization_scope="project",
            event_scope="project",
            category="runtime",
            source="sandbox",
            action=f"skill.script.{status}",
            status="succeeded" if status == "completed" else status,
            risk_level="medium",
            idempotency_key=f"skill-script:{invocation.id}:terminal",
            occurred_at=datetime.now(UTC),
            run_id=invocation.run_id,
            resource_type="skill_script",
            resource_id=invocation.id,
            resource_name=invocation.tool_id,
            summary="Skill script execution finished",
            metadata=metadata,
            allowed_metadata_keys=frozenset(metadata),
            error_code=error_code,
            duration_ms=duration_ms,
        ))
    return True
