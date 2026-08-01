from app.conversations.repository import ConversationRepository

from .model_gateway import ModelGateway, ModelRuntimeError


class PlatformAgentHarness:
    def __init__(
        self,
        repository: ConversationRepository,
        model_gateway: ModelGateway,
    ):
        self.repository = repository
        self.model_gateway = model_gateway

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

        run.status = "running"
        self.repository.append_event(
            run_id, "run.status", {"status": "running"}
        )
        self.repository.session.commit()

        try:
            messages = [
                {"role": message.role, "content": message.content}
                for message in self.repository.get_run_messages(run_id)
                if message.role in {"user", "assistant", "system"}
            ]
            result = self.model_gateway.generate(messages)
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