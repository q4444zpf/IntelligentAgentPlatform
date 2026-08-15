import json
import hashlib
import uuid
from datetime import UTC, datetime
from time import perf_counter

from app.agents.service import AgentNotFoundError, AgentService
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.repository import ConversationRepository
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolDefinition, ToolExecutionContext, ToolRuntimeError
from app.tools.service import ToolService
from app.artifacts.models import ArtifactRecord

from .model_gateway import ModelGateway, ModelRuntimeError, ModelSelection

MAX_CONVERSATION_MESSAGES = 100
MAX_MODEL_ITERATIONS = 4
MAX_TOOL_CALLS = 8


class PlatformAgentHarness:
    def __init__(
        self,
        repository: ConversationRepository,
        model_gateway: ModelGateway,
        agent_service: AgentService,
        *,
        tool_service: ToolService | None = None,
        tool_gateway: ToolGateway | None = None,
        artifact_storage=None,
        checkpoint_store=None,
        audit_recorder: AuditRecorder | None = None,
    ):
        self.repository = repository
        self.model_gateway = model_gateway
        self.agent_service = agent_service
        self.tool_service = tool_service
        self.tool_gateway = tool_gateway
        self.artifact_storage = artifact_storage
        self.checkpoint_store = checkpoint_store
        self.audit_recorder = audit_recorder or AuditRecorder()

    def execute(self, run_id: str) -> None:
        run = self.repository.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.actor_type != "agent":
            self._fail(run_id, "unsupported_actor_type", "当前运行时暂不支持多智能体团队，请选择单个智能体")
            return
        try:
            agent = self.agent_service.get(run.actor_id)
        except AgentNotFoundError:
            self._fail(run_id, "agent_unavailable", "智能体不可用，请检查智能体配置")
            return
        if not agent.enabled:
            self._fail(run_id, "agent_unavailable", "智能体不可用，请检查智能体配置")
            return

        execution_context = self.repository.get_run_execution_context(run_id)
        if execution_context is None:
            raise KeyError(run_id)
        try:
            self._record_agent_status(run_id, "running")
        except Exception:
            self._persist_failure_safely(
                run_id, "audit_persistence_failed", "智能体运行失败，请稍后重试"
            )
            return

        llm_iteration: int | None = None
        llm_started = 0.0
        selection = ModelSelection(agent.provider_id, agent.model)
        try:
            messages = self._build_messages(run_id, agent)
            definitions = self._resolve_tool_definitions(agent.tool_ids)
            authorized_tool_ids = set(agent.tool_ids)
            context = self._execution_context(run_id)
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            usage_seen = {key: False for key in usage}
            total_tool_calls = 0

            for iteration in range(MAX_MODEL_ITERATIONS):
                llm_iteration = iteration
                llm_started = perf_counter()
                result = self.model_gateway.generate(
                    messages,
                    selection,
                    tools=definitions,
                )
                duration_ms = max(0, round((perf_counter() - llm_started) * 1000))
                metadata = {
                    "provider": selection.provider_id,
                    "model": selection.model,
                    "iteration": iteration,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "total_tokens": result.total_tokens,
                }
                calls = result.tool_calls
                if not calls and (result.content is None or not result.content.strip()):
                    raise ModelRuntimeError("empty model response")

                self.audit_recorder.record(self.repository.session, AuditRecordRequest(
                    unit_id=execution_context["unit_id"],
                    project_id=execution_context["project_id"],
                    user_id=execution_context["user_id"],
                    actor_roles=execution_context["actor_roles"],
                    authorization_scope="project",
                    event_scope="project",
                    category="runtime", source="llm", action="llm.invoke.succeeded",
                    status="succeeded", risk_level="low", trace_id=run_id, run_id=run_id,
                    resource_type="model", resource_id=selection.model,
                    idempotency_key=f"llm:{run_id}:{iteration}:succeeded",
                    occurred_at=datetime.now(UTC), duration_ms=duration_ms,
                    metadata=metadata,
                    allowed_metadata_keys=frozenset(metadata),
                ))
                self.repository.session.commit()
                for key in usage:
                    value = getattr(result, key)
                    if value is not None:
                        usage[key] += value
                        usage_seen[key] = True

                if not calls:
                    self._complete(run_id, result.content, usage, usage_seen, messages)
                    return

                if iteration == MAX_MODEL_ITERATIONS - 1:
                    raise ToolRuntimeError(
                        "tool_iteration_limit",
                        "工具调用次数超过平台限制",
                    )

                if total_tool_calls + len(calls) > MAX_TOOL_CALLS:
                    raise ToolRuntimeError("tool_iteration_limit", "工具调用次数超过平台限制")

                messages.append({
                    "role": "assistant",
                    "content": result.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                            },
                        }
                        for call in calls
                    ],
                })
                if self.tool_gateway is None or context is None:
                    raise ToolRuntimeError("tool_execution_failed", "工具执行失败。")
                for call in calls:
                    executed = self.tool_gateway.execute(call, context, authorized_tool_ids)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(executed.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    })
                total_tool_calls += len(calls)

        except ModelRuntimeError:
            self.repository.session.rollback()
            try:
                if llm_iteration is not None:
                    metadata = {
                        "provider": selection.provider_id,
                        "model": selection.model,
                        "iteration": llm_iteration,
                    }
                    self.audit_recorder.record(self.repository.session, AuditRecordRequest(
                        unit_id=execution_context["unit_id"],
                        project_id=execution_context["project_id"],
                        user_id=execution_context["user_id"],
                        actor_roles=execution_context["actor_roles"],
                        authorization_scope="project",
                        event_scope="project",
                        category="runtime", source="llm", action="llm.invoke.failed",
                        status="failed", risk_level="medium", trace_id=run_id, run_id=run_id,
                        resource_type="model", resource_id=selection.model,
                        idempotency_key=f"llm:{run_id}:{llm_iteration}:failed",
                        occurred_at=datetime.now(UTC),
                        duration_ms=max(0, round((perf_counter() - llm_started) * 1000)),
                        metadata=metadata, allowed_metadata_keys=frozenset(metadata),
                        error_code="model_request_failed",
                    ))
            except Exception:
                self.repository.session.rollback()
                self._persist_failure_safely(
                    run_id, "audit_persistence_failed",
                    "智能体运行失败，请稍后重试", rollback=False,
                )
                return
            self._persist_failure_safely(
                run_id, "model_request_failed",
                "模型调用失败，请检查默认模型配置或稍后重试",
                rollback=False,
            )
        except ToolRuntimeError as error:
            self.repository.session.rollback()
            if error.code == "approval_required":
                run = self.repository.get_run_by_id(run_id)
                if run is not None:
                    run.status = "waiting_approval"
                    self.repository.append_event(run_id, "run.status", {"status": "waiting_approval"})
                    if self.checkpoint_store is not None:
                        self.checkpoint_store.save(run_id, "approval", {
                            "run_id": run_id,
                            "status": "waiting_approval",
                            "messages": messages,
                            "iteration": llm_iteration,
                        })
                    self.repository.session.commit()
                return
            self._persist_failure_safely(run_id, error.code, error.safe_message, rollback=False)
        except Exception:
            self.repository.session.rollback()
            self._persist_failure_safely(
                run_id, "runtime_failed", "智能体运行失败，请稍后重试", rollback=False
            )

    def _build_messages(self, run_id, agent):
        conversation_messages = [
            {"role": message.role, "content": message.content}
            for message in self.repository.get_run_messages(run_id)
            if message.role in {"user", "assistant", "system"}
        ][-MAX_CONVERSATION_MESSAGES:]
        messages = [
            *([{"role": "system", "content": agent.system_prompt}] if agent.system_prompt.strip() else []),
            *([{"role": "system", "content": agent.context_prompt}] if agent.context_prompt.strip() else []),
            *conversation_messages,
        ]
        for invocation in self.repository.list_tool_invocations(run_id):
            if invocation.status != "completed" or invocation.result_summary is None:
                continue
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": invocation.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": invocation.tool_id,
                        "arguments": json.dumps(invocation.arguments_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    },
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": invocation.tool_call_id,
                "content": json.dumps(invocation.result_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            })
        if not agent.tool_ids and self.tool_service is not None:
            messages.append({
                "role": "system",
                "content": "当前智能体未授权任何工具，不可调用任何工具；不得声称已经调用工具或编造工具返回结果。",
            })
        return messages

    def _resolve_tool_definitions(self, tool_ids: list[str]) -> list[ToolDefinition]:
        if not tool_ids:
            return []
        if self.tool_service is None:
            raise ToolRuntimeError("tool_execution_failed", "工具执行失败。")
        definitions = []
        for tool_id in tool_ids:
            tool = self.tool_service.get(tool_id)
            if tool.published and tool.enabled:
                definitions.append(ToolDefinition(
                    tool_id=tool.tool_id,
                    description=tool.description,
                    input_schema=tool.input_schema,
                ))
        return definitions

    def _execution_context(self, run_id: str) -> ToolExecutionContext | None:
        value = self.repository.get_run_execution_context(run_id)
        return ToolExecutionContext(**value) if value is not None else None

    def _complete(self, run_id, content, usage, usage_seen, messages=None):
        try:
            assistant_message = self.repository.add_assistant_message(run_id, content)
            self.repository.append_event(run_id, "message.completed", {"message_id": assistant_message.id, "role": "assistant"})
            if any(usage_seen.values()):
                self.repository.append_event(run_id, "run.usage", {key: usage[key] if usage_seen[key] else None for key in usage})
            self._record_agent_status(run_id, "completed", commit=False)
            if self.checkpoint_store is not None and messages is not None:
                self.checkpoint_store.save(run_id, "runtime", {
                    "run_id": run_id,
                    "status": "completed",
                    "messages": [*messages, {"role": "assistant", "content": content}],
                }, commit=False)
            self.repository.session.commit()
        except Exception:
            self._persist_failure_safely(
                run_id, "audit_persistence_failed", "智能体运行失败，请稍后重试"
            )
            return
        if self.artifact_storage is not None and content:
            self._persist_result_artifact_safely(run_id, content)

    def _persist_result_artifact_safely(self, run_id: str, content: str) -> None:
        object_key = None
        try:
            object_key = self._persist_result_artifact(run_id, content)
            self.repository.session.commit()
            return
        except Exception:  # noqa: BLE001
            self.repository.session.rollback()
            if object_key is not None:
                try:
                    self.artifact_storage.delete_object(object_key)
                except Exception:  # noqa: BLE001, S110
                    pass
        self.repository.append_event(
            run_id,
            "artifact.persistence_failed",
            {
                "code": "artifact_persistence_failed",
                "message": "成果文件保存失败",
            },
        )
        self.repository.session.commit()

    def _persist_result_artifact(self, run_id: str, content: str) -> str:
        context = self.repository.get_run_execution_context(run_id)
        if context is None:
            raise KeyError(run_id)
        artifact_id = str(uuid.uuid4())
        object_key = (
            f"units/{context['unit_id']}/projects/{context['project_id']}"
            f"/{artifact_id}/run-result.txt"
        )
        data = content.encode("utf-8")
        self.artifact_storage.put_bytes(object_key, data, "text/plain; charset=utf-8")
        self.repository.session.add(ArtifactRecord(
            id=artifact_id,
            unit_id=context["unit_id"],
            project_id=context["project_id"],
            owner_id=context["user_id"],
            scope="project",
            run_id=run_id,
            object_key=object_key,
            filename="run-result.txt",
            content_type="text/plain; charset=utf-8",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            status="active",
        ))
        return object_key

    def _record_agent_status(
        self, run_id: str, status: str, *, code: str | None = None,
        message: str | None = None, commit: bool = True,
    ) -> None:
        run = self.repository.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = status
        if status == "failed":
            self.repository.append_event(
                run_id, "run.error", {"code": code, "message": message}
            )
        self.repository.append_event(run_id, "run.status", {"status": status})
        context = self.repository.get_run_execution_context(run_id)
        audit_status = {
            "running": "started", "completed": "succeeded", "failed": "failed"
        }[status]
        self.audit_recorder.record(self.repository.session, AuditRecordRequest(
            unit_id=context["unit_id"], project_id=context["project_id"],
            user_id=context["user_id"], category="runtime", source="agent",
            actor_roles=context["actor_roles"],
            authorization_scope="project", event_scope="project",
            action=f"agent.run.{status}", status=audit_status,
            risk_level="medium" if status == "failed" else "low",
            trace_id=run_id, run_id=run_id, resource_type="agent",
            resource_id=run.actor_id,
            idempotency_key=f"agent:{run_id}:status:{status}",
            occurred_at=datetime.now(UTC), error_code=code,
        ))
        if commit:
            self.repository.session.commit()

    def _fail(self, run_id: str, code: str, message: str) -> None:
        self._persist_failure_safely(run_id, code, message)

    def _persist_failure_safely(
        self, run_id: str, code: str, message: str, *, rollback: bool = True,
    ) -> None:
        if rollback:
            self.repository.session.rollback()
        try:
            self._record_agent_status(
                run_id, "failed", code=code, message=message
            )
            return
        except Exception:
            self.repository.session.rollback()

        run = self.repository.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = "failed"
        self.repository.append_event(
            run_id, "run.error",
            {
                "code": "audit_persistence_failed",
                "message": "智能体运行失败，请稍后重试",
            },
        )
        self.repository.append_event(run_id, "run.status", {"status": "failed"})
        self.repository.session.commit()
