from dataclasses import dataclass
from typing import Protocol

import httpx

from app.model_providers.service import ProviderService
from app.model_providers.store import ProviderStore


class ModelRuntimeError(Exception):
    pass


class ModelConfigurationError(ModelRuntimeError):
    pass


class ModelUpstreamError(ModelRuntimeError):
    pass


@dataclass(frozen=True)
class ModelResult:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ModelGateway(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> ModelResult: ...


class OpenAICompatibleModelGateway:
    def __init__(self, store: ProviderStore | None = None):
        self.store = store or ProviderStore()

    def generate(self, messages: list[dict[str, str]]) -> ModelResult:
        service = ProviderService(self.store)
        active = service.get_active()
        if not active.provider_id or not active.model:
            raise ModelConfigurationError("No active model is configured")

        try:
            provider = service.get(active.provider_id)
        except KeyError as error:
            raise ModelConfigurationError("The active provider is unavailable") from error
        model = next(
            (item for item in provider.models if item.id == active.model), None
        )
        if not provider.configured or model is None or not model.enabled:
            raise ModelConfigurationError("The active model is unavailable")
        if provider.protocol != "OpenAIChatModel":
            raise ModelConfigurationError("The active model protocol is unsupported")

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
            "model": active.model,
            "messages": messages,
            "stream": False,
        }

        try:
            with httpx.Client(
                timeout=httpx.Timeout(60.0, connect=10.0), trust_env=False
            ) as client:
                response = client.post(
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content:
                raise ValueError("empty model content")
            usage = data.get("usage") or {}
            return ModelResult(
                content=content,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelUpstreamError("The model request failed") from error
