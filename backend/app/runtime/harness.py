from app.agents.service import AgentNotFoundError, AgentService
from app.conversations.repository import ConversationRepository

from .model_gateway import ModelGateway, ModelRuntimeError, ModelSelection

MAX_CONVERSATION_MESSAGES = 100


class PlatformAgentHarness:
    def __init__(
        self,
        repository: ConversationRepository,
        model_gateway: ModelGateway,
        agent_service: AgentService,
    ):
        self.repository = repository
        self.model_gateway = model_gateway
        self.agent_service = agent_service

    def execute(self, run_id: str) -> None:
        run = self.repository.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.actor_type != "agent":
            self._fail(
                run_id,
                "unsupported_actor_type",
                "当前运行时暂不支持多智能体团队，请选择单个智能体",
            )
            return

        try:
            agent = self.agent_service.get(run.actor_id)
        except AgentNotFoundError:
            self._fail(
                run_id,
                "agent_unavailable",
                "智能体不可用，请检查智能体配置",
            )
            return
        if not agent.enabled:
            self._fail(
                run_id,
                "agent_unavailable",
                "智能体不可用，请检查智能体配置",
            )
            return

        run.status = "running"
        self.repository.append_event(
            run_id, "run.status", {"status": "running"}
        )
        self.repository.session.commit()

        try:
            conversation_messages = [
                {"role": message.role, "content": message.content}
                for message in self.repository.get_run_messages(run_id)
                if message.role in {"user", "assistant", "system"}
            ][-MAX_CONVERSATION_MESSAGES:]
            messages = [
                *(
                    [{"role": "system", "content": agent.system_prompt}]
                    if agent.system_prompt.strip()
                    else []
                ),
                *(
                    [{"role": "system", "content": agent.context_prompt}]
                    if agent.context_prompt.strip()
                    else []
                ),
                *conversation_messages,
            ]
            result = self.model_gateway.generate(
                messages,
                ModelSelection(agent.provider_id, agent.model),
            )
            assistant_message = self.repository.add_assistant_message(
                run_id, result.content
            )
            self.repository.append_event(
                run_id,
                "message.completed",
                {"message_id": assistant_message.id, "role": "assistant"},
            )
            usage = {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            }
            if any(value is not None for value in usage.values()):
                self.repository.append_event(run_id, "run.usage", usage)
            run.status = "completed"
            self.repository.append_event(
                run_id, "run.status", {"status": "completed"}
            )
            self.repository.session.commit()
        except ModelRuntimeError:
            self.repository.session.rollback()
            self._fail(
                run_id,
                "model_request_failed",
                "模型调用失败，请检查默认模型配置或稍后重试",
            )
        except Exception:
            self.repository.session.rollback()
            self._fail(run_id, "runtime_failed", "智能体运行失败，请稍后重试")

    def _fail(self, run_id: str, code: str, message: str) -> None:
        run = self.repository.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = "failed"
        self.repository.append_event(
            run_id, "run.error", {"code": code, "message": message}
        )
        self.repository.append_event(
            run_id, "run.status", {"status": "failed"}
        )
        self.repository.session.commit()