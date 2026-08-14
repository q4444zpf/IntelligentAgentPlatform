from __future__ import annotations

import time
from threading import Lock
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.models import AgentRun, RunEvent
from app.conversations.repository import ConversationRepository

from .workflow_runner import RunnerUnavailableError, WorkflowRunnerClient

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_GATEWAY_FINAL_STATUSES = _TERMINAL_STATUSES | {"waiting_approval"}
_SAFE_ERRORS = {
    "sandbox_failed": "沙箱任务执行失败",
    "sandbox_oom": "沙箱任务超过内存限制",
    "sandbox_cancelled": "沙箱任务已取消",
    "sandbox_timeout": "沙箱任务执行超时",
    "launcher_unavailable": "沙箱运行服务暂不可用",
}
_RUNNER_ACTIONS = {
    "snapshot.read",
    "model.invoke",
    "tool.invoke",
    "checkpoint.read",
    "checkpoint.write",
    "event.append",
    "artifact.create",
    "result.complete",
}


class SandboxRunCoordinator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        runner: WorkflowRunnerClient,
        *,
        poll_interval: float = 0.25,
        timeout_seconds: float = 300,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        audit_recorder: AuditRecorder | None = None,
        snapshot_service_factory: Callable[[Session], Any] | None = None,
        token_service_factory: Callable[[Session], Any] | None = None,
        gateway_url: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.runner = runner
        self.poll_interval = max(0, poll_interval)
        self.timeout_seconds = max(0, timeout_seconds)
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.snapshot_service_factory = snapshot_service_factory
        self.token_service_factory = token_service_factory
        self.gateway_url = gateway_url.rstrip("/")
        self.clock = clock or (lambda: datetime.now(UTC))
        self._cleanup_locks: dict[str, Lock] = {}

    def execute(self, run_id: str) -> None:
        if not self._start(run_id):
            return
        started_at = self.monotonic()
        deadline_at = self.clock() + timedelta(seconds=self.timeout_seconds)
        try:
            snapshot, token = self._prepare_execution(run_id, deadline_at)
            self.runner.submit(
                run_id,
                self._agent_version(run_id),
                "runtime",
                snapshot_id=snapshot.snapshot_id,
                snapshot_digest=snapshot.digest,
                gateway_url=self.gateway_url,
                run_token=token.value,
                deadline_at=deadline_at.isoformat(),
            )
            while True:
                if self.monotonic() - started_at >= self.timeout_seconds:
                    self._terminate_safely(run_id)
                    self._finish(run_id, "failed", "sandbox_timeout")
                    return
                status = self.runner.status(run_id)
                container_status = status.get("status")
                if container_status in {"running", "created", "accepted"}:
                    if self.poll_interval:
                        self.sleeper(self.poll_interval)
                    continue
                self._apply_container_status(run_id, status)
                return
        except RunnerUnavailableError:
            self._finish(run_id, "failed", "launcher_unavailable")
        finally:
            self._revoke_for_terminal_run(run_id)
            self._cleanup(run_id)

    def cancel(self, run_id: str) -> None:
        if self._is_terminal(run_id):
            return
        self._terminate_safely(run_id)
        self._finish(run_id, "cancelled", "sandbox_cancelled")
        self._revoke_for_terminal_run(run_id)
        self._cleanup(run_id)

    def recover(self, run_id: str) -> None:
        if self._is_terminal(run_id):
            return
        try:
            self._apply_container_status(run_id, self.runner.status(run_id))
        except RunnerUnavailableError:
            self._finish(run_id, "failed", "launcher_unavailable")
        finally:
            self._revoke_for_terminal_run(run_id)
            self._cleanup(run_id)

    def _prepare_execution(self, run_id: str, deadline_at: datetime):
        if (
            self.snapshot_service_factory is None
            or self.token_service_factory is None
            or not self.gateway_url
        ):
            raise RuntimeError("sandbox execution gateway is not configured")
        with self.session_factory() as session:
            snapshot = self.snapshot_service_factory(session).create(run_id)
            token = self.token_service_factory(session).issue(
                snapshot,
                _RUNNER_ACTIONS,
                deadline_at,
            )
            session.commit()
            return snapshot, token

    def _revoke_for_terminal_run(self, run_id: str) -> None:
        if self.token_service_factory is None:
            return
        with self.session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status not in _GATEWAY_FINAL_STATUSES:
                return
            latest_error = session.scalar(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.event_type == "run.error")
                .order_by(RunEvent.sequence.desc())
                .limit(1)
            )
            latest_completion = session.scalar(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "runner.completion",
                )
                .order_by(RunEvent.sequence.desc())
                .limit(1)
            )
            reason = run.status
            if latest_completion is not None:
                reason = latest_completion.payload.get("error_code") or reason
            if latest_error is not None:
                reason = latest_error.payload.get("code") or reason
            self.token_service_factory(session).revoke(run_id, reason)
            session.commit()

    def list_recoverable_run_ids(self) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(AgentRun.id)
                    .where(AgentRun.status == "running")
                    .order_by(AgentRun.created_at, AgentRun.id)
                )
            )

    def list_cleanup_retry_run_ids(self) -> list[str]:
        with self.session_factory() as session:
            candidates = list(
                session.scalars(
                    select(AgentRun.id)
                    .where(AgentRun.status.in_(_TERMINAL_STATUSES))
                    .order_by(AgentRun.created_at, AgentRun.id)
                )
            )
            retry_ids = []
            for run_id in candidates:
                latest = session.scalar(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.event_type == "sandbox.cleanup",
                    )
                    .order_by(RunEvent.sequence.desc())
                    .limit(1)
                )
                if latest is not None and latest.payload.get("status") == "failed":
                    retry_ids.append(run_id)
            return retry_ids

    def retry_cleanup(self, run_id: str) -> None:
        self._cleanup(run_id)

    def _start(self, run_id: str) -> bool:
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            run = repository.get_run_by_id(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status in _GATEWAY_FINAL_STATUSES:
                return False
            if run.status == "running":
                return False
            repository.append_event(run_id, "sandbox.started", {"status": "started"})
            run.status = "running"
            repository.append_event(run_id, "run.status", {"status": "running"})
            self._record_audit(session, repository, run, "sandbox.run.started", "started")
            session.commit()
            return True

    def _is_terminal(self, run_id: str) -> bool:
        with self.session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise KeyError(run_id)
            return run.status in _TERMINAL_STATUSES

    def _apply_container_status(self, run_id: str, status: dict) -> None:
        container_status = status.get("status")
        if container_status == "terminated":
            self._finish(run_id, "cancelled", "sandbox_cancelled")
        elif container_status == "exited" and status.get("oom_killed") is True:
            self._finish(run_id, "failed", "sandbox_oom")
        elif container_status == "exited" and status.get("exit_code") == 0:
            self._finish(run_id, "completed")
        else:
            self._finish(run_id, "failed", "sandbox_failed")

    def _agent_version(self, run_id: str) -> str:
        with self.session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise KeyError(run_id)
            return run.actor_id

    def _finish(self, run_id: str, status: str, error_code: str | None = None) -> None:
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            run = repository.get_run_by_id(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status in _GATEWAY_FINAL_STATUSES:
                return
            repository.append_event(
                run_id,
                "sandbox.finished",
                {"status": status, **({"code": error_code} if error_code else {})},
            )
            if error_code:
                repository.append_event(
                    run_id,
                    "run.error",
                    {"code": error_code, "message": _SAFE_ERRORS[error_code]},
                )
            run.status = status
            repository.append_event(run_id, "run.status", {"status": status})
            audit_status = {
                "completed": "succeeded",
                "failed": "failed",
                "cancelled": "cancelled",
            }[status]
            self._record_audit(
                session,
                repository,
                run,
                f"sandbox.run.{status}",
                audit_status,
                error_code=error_code,
            )
            session.commit()

    def _cleanup(self, run_id: str) -> None:
        lock = self._cleanup_locks.setdefault(run_id, Lock())
        with lock:
            with self.session_factory() as session:
                latest = session.scalar(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.event_type == "sandbox.cleanup",
                    )
                    .order_by(RunEvent.sequence.desc())
                    .limit(1)
                )
                if latest is not None and latest.payload.get("status") == "cleaned":
                    return

            payload = {"status": "cleaned"}
            try:
                self.runner.cleanup(run_id)
            except RunnerUnavailableError:
                payload = {"status": "failed", "code": "launcher_unavailable"}
            with self.session_factory() as session:
                repository = ConversationRepository(session)
                if repository.get_run_by_id(run_id) is not None:
                    repository.append_event(run_id, "sandbox.cleanup", payload)
                    session.commit()

    def _terminate_safely(self, run_id: str) -> None:
        try:
            self.runner.terminate(run_id)
        except RunnerUnavailableError:
            return

    def _record_audit(
        self,
        session: Session,
        repository: ConversationRepository,
        run: AgentRun,
        action: str,
        status: str,
        *,
        error_code: str | None = None,
    ) -> None:
        context = repository.get_run_execution_context(run.id)
        if context is None:
            raise KeyError(run.id)
        self.audit_recorder.record(
            session,
            AuditRecordRequest(
                unit_id=str(context["unit_id"]),
                project_id=str(context["project_id"]),
                user_id=str(context["user_id"]),
                actor_roles=tuple(context["actor_roles"]),
                authorization_scope="project",
                event_scope="project",
                category="runtime",
                source="sandbox",
                action=action,
                status=status,
                risk_level="medium" if error_code else "low",
                trace_id=run.id,
                run_id=run.id,
                resource_type="agent",
                resource_id=run.actor_id,
                idempotency_key=f"sandbox:{run.id}:{action}",
                occurred_at=datetime.now(UTC),
                error_code=error_code,
            ),
        )
