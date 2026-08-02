import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.model_providers.service import ProviderService
from app.model_providers.store import ProviderStore
from app.tools.schemas import ToolCall, ToolDefinition

MAX_MODEL_TOOL_CALLS = 8
MAX_TOOL_ARGUMENT_BYTES = 64 * 1024
MAX_TOOL_ARGUMENT_DEPTH = 20
MAX_TOOL_CALL_ID_LENGTH = 128
MAX_TOOL_WIRE_NAME_LENGTH = 64
TOOL_WIRE_HASH_LENGTH = 16


class ModelRuntimeError(Exception):
    pass


class ModelConfigurationError(ModelRuntimeError):
    pass


class ModelUpstreamError(ModelRuntimeError):
    pass


@dataclass(frozen=True)
class ModelResult:
    content: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ModelSelection:
    provider_id: str = ""
    model: str = ""


class ModelGateway(Protocol):
    def generate(
        self,
        messages: list[dict[str, Any]],
        selection: ModelSelection | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ModelResult: ...


def build_runtime_messages(
    provider_id: str,
    model: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    identity = (
        "Runtime model identity (authoritative platform configuration): "
        f"provider_id={provider_id}, model={model}. "
        "When asked about model identity, use exactly this configuration and "
        "do not guess or claim another model."
    )
    return [{"role": "system", "content": identity}, *messages]


def build_tool_wire_name(tool_id: str) -> str:
    digest = hashlib.sha256(tool_id.encode("utf-8")).hexdigest()
    suffix = digest[:TOOL_WIRE_HASH_LENGTH]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", tool_id).strip("_") or "tool"
    slug_limit = MAX_TOOL_WIRE_NAME_LENGTH - TOOL_WIRE_HASH_LENGTH - 1
    return f"{slug[:slug_limit]}_{suffix}"


def _encode_tool_history_messages(
    messages: list[dict[str, Any]],
    internal_to_wire: dict[str, str],
) -> list[dict[str, Any]]:
    encoded_messages = []
    for message in messages:
        if not isinstance(message, dict):
            raise ModelConfigurationError("Invalid model message history")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str):
            raise ModelConfigurationError("Invalid model message history")
        if content is not None and not isinstance(content, str):
            raise ModelConfigurationError("Invalid model message history")

        encoded_message = dict(message)
        if role == "assistant" and "tool_calls" in message:
            raw_calls = message["tool_calls"]
            if not isinstance(raw_calls, list):
                raise ModelConfigurationError("Invalid tool call history")
            encoded_calls = []
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    raise ModelConfigurationError("Invalid tool call history")
                call_id = raw_call.get("id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ModelConfigurationError("Invalid tool call history")
                if raw_call.get("type") != "function":
                    raise ModelConfigurationError("Invalid tool call history")
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    raise ModelConfigurationError("Invalid tool call history")
                internal_name = function.get("name")
                arguments = function.get("arguments")
                if not isinstance(internal_name, str) or not internal_name.strip():
                    raise ModelConfigurationError("Invalid tool call history")
                if not isinstance(arguments, str):
                    raise ModelConfigurationError("Invalid tool call history")
                wire_name = internal_to_wire.get(internal_name)
                if wire_name is None:
                    raise ModelConfigurationError(
                        "Historical tool is unavailable"
                    )
                encoded_function = dict(function)
                encoded_function["name"] = wire_name
                encoded_call = dict(raw_call)
                encoded_call["function"] = encoded_function
                encoded_calls.append(encoded_call)
            encoded_message["tool_calls"] = encoded_calls
        elif role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise ModelConfigurationError("Invalid tool message history")
            if not isinstance(content, str):
                raise ModelConfigurationError("Invalid tool message history")

        encoded_messages.append(encoded_message)
    return encoded_messages


def _validate_argument_depth(value: dict[str, Any]) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, parent_depth = stack.pop()
        if isinstance(item, dict):
            depth = parent_depth + 1
            if depth > MAX_TOOL_ARGUMENT_DEPTH:
                raise ValueError("tool arguments are too deeply nested")
            stack.extend((child, depth) for child in item.values())
        elif isinstance(item, list):
            depth = parent_depth + 1
            if depth > MAX_TOOL_ARGUMENT_DEPTH:
                raise ValueError("tool arguments are too deeply nested")
            stack.extend((child, depth) for child in item)


class OpenAICompatibleModelGateway:
    def __init__(self, store: ProviderStore | None = None):
        self.store = store or ProviderStore()

    @staticmethod
    def _resolve_selection(
        service: ProviderService,
        selection: ModelSelection | None,
    ):
        if selection and selection.provider_id and selection.model:
            provider_id = selection.provider_id
            model_id = selection.model
        else:
            active = service.get_active()
            provider_id = active.provider_id
            model_id = active.model

        if not provider_id or not model_id:
            raise ModelConfigurationError("No active model is configured")
        try:
            provider = service.get(provider_id)
        except KeyError as error:
            raise ModelConfigurationError(
                "The selected provider is unavailable"
            ) from error

        model = next(
            (item for item in provider.models if item.id == model_id),
            None,
        )
        if (
            not provider.configured
            or not provider.enabled
            or model is None
            or not model.enabled
        ):
            raise ModelConfigurationError("The selected model is unavailable")
        if provider.protocol != "OpenAIChatModel":
            raise ModelConfigurationError(
                "The selected model protocol is unsupported"
            )
        return provider_id, model_id, provider, model

    def generate(
        self,
        messages: list[dict[str, Any]],
        selection: ModelSelection | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ModelResult:
        service = ProviderService(self.store)
        provider_id, model_id, provider, model = self._resolve_selection(
            service,
            selection,
        )

        state = self.store.load()
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        saved = state.get(bucket_name, {}).get(provider.id, {})
        api_key = saved.get("api_key", "")
        headers = {"Content-Type": "application/json", **provider.custom_headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        wire_to_internal: dict[str, str] = {}
        internal_to_wire: dict[str, str] = {}
        payload_tools: list[dict[str, Any]] = []
        for tool in tools or []:
            wire_name = build_tool_wire_name(tool.tool_id)
            mapped_id = wire_to_internal.get(wire_name)
            if mapped_id is not None and mapped_id != tool.tool_id:
                raise ModelConfigurationError("Tool names conflict")
            wire_to_internal[wire_name] = tool.tool_id
            internal_to_wire[tool.tool_id] = wire_name
            payload_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": wire_name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )

        encoded_messages = _encode_tool_history_messages(
            messages,
            internal_to_wire,
        )
        payload = {
            **provider.generate_kwargs,
            **model.extra_config,
            "model": model_id,
            "messages": build_runtime_messages(
                provider_id,
                model_id,
                encoded_messages,
            ),
            "stream": False,
        }
        if payload_tools:
            payload["tools"] = payload_tools
            payload["tool_choice"] = "auto"

        try:
            with httpx.Client(
                timeout=httpx.Timeout(60.0, connect=10.0),
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid model response")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("invalid model choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError("invalid model choice")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ValueError("invalid model message")

            raw_tool_calls = message.get("tool_calls", [])
            if not isinstance(raw_tool_calls, list):
                raise ValueError("invalid model tool calls")
            if len(raw_tool_calls) > MAX_MODEL_TOOL_CALLS:
                raise ValueError("too many model tool calls")

            tool_calls: list[ToolCall] = []
            seen_call_ids: set[str] = set()
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    raise ValueError("invalid model tool call")
                if raw_call.get("type") != "function":
                    raise ValueError("invalid model tool call type")
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    raise ValueError("invalid model tool function")
                raw_arguments = function.get("arguments")
                if not isinstance(raw_arguments, str):
                    raise ValueError("invalid model tool arguments")
                if len(raw_arguments.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
                    raise ValueError("model tool arguments are too large")
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                _validate_argument_depth(arguments)

                call_id = raw_call.get("id")
                wire_name = function.get("name")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("tool call id is invalid")
                if len(call_id) > MAX_TOOL_CALL_ID_LENGTH:
                    raise ValueError("tool call id is too long")
                if call_id in seen_call_ids:
                    raise ValueError("duplicate tool call id")
                if not isinstance(wire_name, str) or not wire_name.strip():
                    raise ValueError("tool call name is invalid")
                internal_name = wire_to_internal.get(wire_name)
                if internal_name is None:
                    raise ValueError("unknown tool call name")
                seen_call_ids.add(call_id)
                tool_calls.append(ToolCall(call_id, internal_name, arguments))

            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("invalid model content")
            if content == "":
                content = None
            if content is None and not tool_calls:
                raise ValueError("empty model content")

            usage = data["usage"] if "usage" in data else {}
            if not isinstance(usage, dict):
                raise ValueError("invalid model usage")
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                if field in usage and (
                    type(usage[field]) is not int or usage[field] < 0
                ):
                    raise ValueError("invalid model usage token count")
            return ModelResult(
                content=content,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                tool_calls=tuple(tool_calls),
            )
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            UnicodeError,
            ValueError,
            RecursionError,
        ) as error:
            raise ModelUpstreamError("The model request failed") from error
