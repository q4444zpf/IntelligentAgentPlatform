from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field, PrivateAttr

from .runner_gateway_schemas import ModelInvocationResponse

_SAFE_MESSAGES = {
    "model_request_failed": "模型调用失败",
    "run_token_invalid": "Runner 凭证无效",
    "run_token_expired": "Runner 凭证已过期",
    "runner_action_forbidden": "Runner 操作未授权",
    "run_not_found": "Run 不存在",
    "idempotency_conflict": "模型调用幂等键冲突",
    "gateway_unavailable": "Runner Gateway 不可用",
    "gateway_response_invalid": "Runner Gateway 返回无效响应",
}


class RunnerGatewayModelError(RuntimeError):
    def __init__(self, code: str, _detail: str | None = None) -> None:
        self.code = code if code in _SAFE_MESSAGES else "gateway_unavailable"
        super().__init__(_SAFE_MESSAGES[self.code])


class ModelInvocationTransport(Protocol):
    def invoke_model(
        self, request: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]: ...


@dataclass
class GatewayModelHttpTransport:
    base_url: str
    run_id: str
    token: str = field(repr=False)
    timeout_seconds: float = 90.0

    def invoke_model(
        self, request: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": idempotency_key,
        }
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                trust_env=False,
            ) as client:
                response = client.post(
                    (
                        f"{self.base_url.rstrip('/')}/runs/"
                        f"{self.run_id}/model-invocations"
                    ),
                    headers=headers,
                    json=request,
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("invalid gateway response")
            return payload
        except httpx.HTTPStatusError as error:
            code = "gateway_unavailable"
            try:
                payload = error.response.json()
                if isinstance(payload, dict) and isinstance(
                    payload.get("code"), str
                ):
                    code = payload["code"]
            except (TypeError, ValueError):
                pass
            raise RunnerGatewayModelError(code) from error
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise RunnerGatewayModelError("gateway_unavailable") from error


class GatewayChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    transport: Any = Field(exclude=True, repr=False)
    _next_invocation_sequence: int = PrivateAttr(default=0)

    def __init__(self, transport: ModelInvocationTransport, **data: Any) -> None:
        super().__init__(transport=transport, **data)

    @property
    def _llm_type(self) -> str:
        return "iap-runner-gateway"

    def bind_tools(self, tools: list[Any], **kwargs: Any):
        normalized = [_normalize_tool(tool) for tool in tools]
        return self.bind(tools=normalized, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        sequence = self._next_invocation_sequence
        self._next_invocation_sequence += 1
        tools = [_normalize_tool(tool) for tool in kwargs.get("tools", [])]
        request = {
            "messages": [_normalize_message(message) for message in messages],
            "tools": tools,
            "temperature": kwargs.get("temperature"),
            "max_output_tokens": kwargs.get(
                "max_output_tokens", kwargs.get("max_tokens")
            ),
            "invocation_sequence": sequence,
        }
        try:
            raw_response = self.transport.invoke_model(
                request,
                f"model-{sequence}",
            )
            response = ModelInvocationResponse.model_validate(raw_response)
        except RunnerGatewayModelError as error:
            raise RunnerGatewayModelError(error.code) from error
        except Exception as error:
            raise RunnerGatewayModelError("gateway_response_invalid") from error

        usage_metadata = None
        if any(
            value is not None
            for value in (
                response.prompt_tokens,
                response.completion_tokens,
                response.total_tokens,
            )
        ):
            input_tokens = response.prompt_tokens or 0
            output_tokens = response.completion_tokens or 0
            usage_metadata = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": (
                    response.total_tokens
                    if response.total_tokens is not None
                    else input_tokens + output_tokens
                ),
            }
        message = AIMessage(
            content=response.content or "",
            tool_calls=[
                {
                    "id": call.id,
                    "name": call.name,
                    "args": call.arguments,
                    "type": "tool_call",
                }
                for call in response.tool_calls
            ],
            usage_metadata=usage_metadata,
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={
                "token_usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                }
            },
        )


def _normalize_message(message: BaseMessage) -> dict[str, Any]:
    if message.type == "human":
        role = "user"
    elif message.type == "ai":
        role = "assistant"
    elif message.type == "system":
        role = "system"
    elif message.type == "tool":
        role = "tool"
    else:
        role = message.type
    normalized: dict[str, Any] = {"role": role, "content": message.content}
    if isinstance(message, AIMessage) and message.tool_calls:
        normalized["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": _json_arguments(call["args"]),
                },
            }
            for call in message.tool_calls
        ]
    if isinstance(message, ToolMessage):
        normalized["tool_call_id"] = message.tool_call_id
    return normalized


def _json_arguments(arguments: dict[str, Any]) -> str:
    import json

    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_tool(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict) and {
        "tool_id",
        "description",
        "input_schema",
    } <= tool.keys():
        return {
            "tool_id": tool["tool_id"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
        }
    converted = convert_to_openai_tool(tool)
    function = converted["function"]
    return {
        "tool_id": function["name"],
        "description": function.get("description", ""),
        "input_schema": function.get("parameters", {"type": "object"}),
    }
