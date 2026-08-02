import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.model_providers.service import ProviderService
from app.model_providers.store import ProviderStore
from app.tools.schemas import ToolCall, ToolDefinition


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
        payload = {
            **provider.generate_kwargs,
            **model.extra_config,
            "model": model_id,
            "messages": build_runtime_messages(
                provider_id,
                model_id,
                messages,
            ),
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.tool_id,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
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
            message = data["choices"][0]["message"]
            content = message.get("content")
            tool_calls = []
            for raw_call in message.get("tool_calls") or []:
                function = raw_call["function"]
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                call_id = raw_call["id"]
                name = function["name"]
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("tool call id is invalid")
                if not isinstance(name, str) or not name:
                    raise ValueError("tool call name is invalid")
                tool_calls.append(ToolCall(call_id, name, arguments))
            if content is not None and not isinstance(content, str):
                raise ValueError("invalid model content")
            if content == "":
                content = None
            if content is None and not tool_calls:
                raise ValueError("empty model content")
            usage = data.get("usage") or {}
            return ModelResult(
                content=content,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                tool_calls=tuple(tool_calls),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelUpstreamError("The model request failed") from error
