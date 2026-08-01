import time
from copy import deepcopy

import httpx

from .registry import builtin_providers
from .schemas import ActiveModel, AddModelRequest, CreateProviderRequest, DiscoverModelsResponse, ModelConfigRequest, ModelInfo, ProbeMultimodalResponse, ProviderConfigRequest, ProviderInfo, TestConnectionResponse
from .store import ProviderStore


class ProviderService:
    def __init__(self, store: ProviderStore | None = None):
        self.store = store or ProviderStore()

    @staticmethod
    def _mask(secret: str) -> str:
        if not secret:
            return ""
        return f"{secret[:3]}••••••••{secret[-4:]}" if len(secret) > 7 else "••••••••"

    def _merged(self) -> tuple[dict[str, ProviderInfo], dict]:
        state = self.store.load()
        result = deepcopy(builtin_providers())
        for provider_id, saved in state.get("providers", {}).items():
            provider = result.get(provider_id)
            if not provider:
                continue
            provider.base_url = saved.get("base_url", provider.base_url)
            provider.protocol = saved.get("protocol", provider.protocol)
            provider.generate_kwargs = saved.get("generate_kwargs", {})
            provider.custom_headers = saved.get("custom_headers", {})
            provider.auth_mode = saved.get("auth_mode", provider.auth_mode)
            provider.masked_api_key = self._mask(saved.get("api_key", ""))
            provider.enabled = saved.get("enabled", True)
            provider.configured = bool(provider.enabled and provider.base_url and (saved.get("api_key") or not provider.require_api_key))
            extra_models = [ModelInfo.model_validate(item) for item in saved.get("extra_models", [])]
            configs = saved.get("model_configs", {})
            for current in provider.models + extra_models:
                if current.id in configs:
                    current = current.__class__.model_validate({**current.model_dump(), **configs[current.id]})
                    for index, item in enumerate(provider.models):
                        if item.id == current.id: provider.models[index] = current
                    for index, item in enumerate(extra_models):
                        if item.id == current.id: extra_models[index] = current
            provider.models.extend(extra_models)
        for provider_id, saved in state.get("custom_providers", {}).items():
            provider = ProviderInfo.model_validate(saved["definition"])
            provider.generate_kwargs = saved.get("generate_kwargs", {})
            provider.custom_headers = saved.get("custom_headers", {})
            provider.auth_mode = saved.get("auth_mode", provider.auth_mode)
            provider.masked_api_key = self._mask(saved.get("api_key", ""))
            provider.enabled = saved.get("enabled", True)
            provider.configured = bool(provider.enabled and provider.base_url and (saved.get("api_key") or not provider.require_api_key))
            provider.models = [ModelInfo.model_validate(item) for item in saved.get("models", [])]
            result[provider_id] = provider
        return result, state

    def list(self) -> list[ProviderInfo]:
        providers, _ = self._merged()
        return list(providers.values())

    def get(self, provider_id: str) -> ProviderInfo:
        providers, _ = self._merged()
        if provider_id not in providers: raise KeyError(provider_id)
        return providers[provider_id]

    def configure(self, provider_id: str, body: ProviderConfigRequest) -> ProviderInfo:
        providers, state = self._merged()
        if provider_id not in providers: raise KeyError(provider_id)
        provider = providers[provider_id]
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        if provider.is_custom: bucket.setdefault("definition", provider.model_dump(exclude={"masked_api_key", "configured", "models"})); bucket.setdefault("models", [m.model_dump() for m in provider.models])
        bucket["base_url"] = body.base_url.strip()
        if provider.is_custom: bucket["definition"]["base_url"] = body.base_url.strip()
        if "api_key" in body.model_fields_set: bucket["api_key"] = body.api_key or ""
        if body.protocol:
            bucket["protocol"] = body.protocol
            if provider.is_custom: bucket["definition"]["protocol"] = body.protocol
        if body.name and provider.is_custom: bucket["definition"]["name"] = body.name.strip()
        bucket["generate_kwargs"] = body.generate_kwargs
        bucket["custom_headers"] = body.custom_headers
        bucket["auth_mode"] = body.auth_mode
        bucket["enabled"] = body.enabled if body.enabled is not None else True
        self.store.save(state)
        return self.get(provider_id)

    def create(self, body: CreateProviderRequest) -> ProviderInfo:
        providers, state = self._merged()
        if body.id in providers: raise ValueError("Provider ID already exists")
        provider = ProviderInfo(id=body.id, name=body.name.strip(), kind="local", base_url=body.default_base_url.strip(), protocol=body.protocol, is_custom=True)
        state.setdefault("custom_providers", {})[body.id] = {"definition": provider.model_dump(exclude={"masked_api_key", "configured", "models"}), "api_key": "", "api_key_prefix": body.api_key_prefix, "models": []}
        self.store.save(state)
        return self.get(body.id)

    def add_model(self, provider_id: str, body: AddModelRequest) -> ProviderInfo:
        provider, state = self.get(provider_id), self.store.load()
        if any(item.id == body.id for item in provider.models): raise ValueError("Model ID already exists")
        model = ModelInfo(id=body.id, name=body.name or body.id, type=body.type, builtin=False)
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        key = "models" if provider.is_custom else "extra_models"
        bucket.setdefault(key, []).append(model.model_dump())
        self.store.save(state)
        return self.get(provider_id)

    def configure_model(self, provider_id: str, model_id: str, body: ModelConfigRequest) -> ProviderInfo:
        provider, state = self.get(provider_id), self.store.load()
        if not any(item.id == model_id for item in provider.models): raise KeyError(model_id)
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        bucket.setdefault("model_configs", {}).setdefault(model_id, {}).update(body.model_dump())
        if provider.is_custom:
            for item in bucket.get("models", []):
                if item["id"] == model_id: item.update(body.model_dump())
        self.store.save(state)
        return self.get(provider_id)

    def remove_model(self, provider_id: str, model_id: str) -> ProviderInfo:
        provider, state = self.get(provider_id), self.store.load()
        model = next((item for item in provider.models if item.id == model_id), None)
        if model is None: raise KeyError(model_id)
        if model.builtin: raise ValueError("Built-in models cannot be removed")
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        key = "models" if provider.is_custom else "extra_models"
        bucket[key] = [item for item in bucket.get(key, []) if item.get("id") != model_id]
        bucket.get("model_configs", {}).pop(model_id, None)
        active = state.get("active_model", {})
        if active.get("provider_id") == provider_id and active.get("model") == model_id:
            state["active_model"] = {}
        self.store.save(state)
        return self.get(provider_id)

    async def discover_models(self, provider_id: str, save: bool = True) -> DiscoverModelsResponse:
        provider, state = self._merged(); item = provider.get(provider_id)
        if item is None: raise KeyError(provider_id)
        if not item.support_model_discovery: raise ValueError("This provider does not support model discovery")
        saved = state.get("custom_providers" if item.is_custom else "providers", {}).get(provider_id, {})
        api_key = saved.get("api_key", "")
        if item.require_api_key and not api_key: raise ValueError("Please configure API Key first")
        headers: dict[str, str] = dict(item.custom_headers)
        if api_key: headers["Authorization"] = f"Bearer {api_key}"
        base_url = item.base_url.rstrip("/")
        url = base_url + ("/api/tags" if item.id == "ollama" and not base_url.endswith("/v1") else "/models")
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"Model discovery failed: {exc}") from exc
        raw_models = payload.get("models", []) if item.id == "ollama" and not base_url.endswith("/v1") else payload.get("data", [])
        discovered = [
            ModelInfo(id=str(raw.get("id") or raw.get("model") or raw.get("name")), name=str(raw.get("name") or raw.get("id") or raw.get("model")), builtin=False, probe_source="discovered")
            for raw in raw_models if raw.get("id") or raw.get("model") or raw.get("name")
        ]
        existing_ids = {model.id for model in item.models}
        additions = [model for model in discovered if model.id not in existing_ids]
        if save and additions:
            bucket_name = "custom_providers" if item.is_custom else "providers"
            bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
            key = "models" if item.is_custom else "extra_models"
            bucket.setdefault(key, []).extend(model.model_dump() for model in additions)
            self.store.save(state)
        return DiscoverModelsResponse(models=discovered, discovered_count=len(discovered), added_count=len(additions) if save else 0)

    async def probe_multimodal(self, provider_id: str, model_id: str) -> ProbeMultimodalResponse:
        provider, state = self._merged(); item = provider.get(provider_id)
        if item is None: raise KeyError(provider_id)
        if not any(model.id == model_id for model in item.models): raise KeyError(model_id)
        saved = state.get("custom_providers" if item.is_custom else "providers", {}).get(provider_id, {})
        api_key = saved.get("api_key", "")
        if item.require_api_key and not api_key: raise ValueError("Please configure API Key first")
        headers = {"Content-Type": "application/json", **item.custom_headers}
        if api_key: headers["Authorization"] = f"Bearer {api_key}"
        base_url = item.base_url.rstrip("/")
        if item.id == "ollama":
            native_base_url = base_url[:-3] if base_url.endswith("/v1") else base_url
            try:
                async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                    response = await client.post(
                        native_base_url + "/api/show",
                        json={"name": model_id},
                    )
                response.raise_for_status()
                capabilities = response.json().get("capabilities", [])
                supports_image = "vision" in capabilities
                result = ProbeMultimodalResponse(
                    supports_image=supports_image,
                    supports_video=False,
                    supports_multimodal=supports_image,
                    image_message="Ollama 模型声明支持 vision" if supports_image else "Ollama 模型能力中未声明 vision",
                    video_message="Ollama 模型能力中未声明视频输入",
                )
            except (httpx.HTTPError, ValueError) as exc:
                raise ValueError(f"Ollama capability probe failed: {exc}") from exc
            self._save_probe_result(state, item, model_id, result)
            return result
        url = base_url + "/chat/completions"
        # Transparent 1x1 PNG. It is sufficient to verify whether the upstream
        # accepts OpenAI-compatible image content without uploading user data.
        pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Reply with OK if you can inspect this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pixel}"}},
            ]}],
            "max_tokens": 8,
            "stream": False,
        }
        supports_image = False; image_message = ""
        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                response = await client.post(url, headers=headers, json=payload)
            supports_image = 200 <= response.status_code < 300
            image_message = "图片输入测试通过" if supports_image else f"图片输入测试失败：HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            image_message = f"图片输入测试失败：{exc.__class__.__name__}"
        # Video has no portable OpenAI-compatible request representation. Keep
        # it false until a provider-specific probe is implemented.
        result = ProbeMultimodalResponse(
            supports_image=supports_image,
            supports_video=False,
            supports_multimodal=supports_image,
            image_message=image_message,
            video_message="当前协议不支持通用视频能力探测",
        )
        self._save_probe_result(state, item, model_id, result)
        return result

    def _save_probe_result(self, state: dict, item: ProviderInfo, model_id: str, result: ProbeMultimodalResponse) -> None:
        bucket_name = "custom_providers" if item.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(item.id, {})
        config = bucket.setdefault("model_configs", {}).setdefault(model_id, {})
        config.update({"supports_image": result.supports_image, "supports_video": result.supports_video, "supports_multimodal": result.supports_multimodal, "probe_source": "probed"})
        if item.is_custom:
            for model in bucket.get("models", []):
                if model.get("id") == model_id: model.update(config)
        else:
            for model in bucket.get("extra_models", []):
                if model.get("id") == model_id: model.update(config)
        self.store.save(state)

    def get_active(self) -> ActiveModel:
        return ActiveModel.model_validate(self.store.load().get("active_model") or {})

    def set_active(self, active: ActiveModel) -> ActiveModel:
        provider = self.get(active.provider_id)
        if not provider.configured or not any(model.id == active.model and model.enabled for model in provider.models): raise ValueError("Provider or model is unavailable")
        state = self.store.load(); state["active_model"] = active.model_dump(); self.store.save(state); return active

    async def test(self, provider_id: str, model_id: str | None = None, override: ProviderConfigRequest | None = None) -> TestConnectionResponse:
        provider, state = self._merged(); item = provider.get(provider_id)
        if not item: raise KeyError(provider_id)
        saved = state.get("custom_providers" if item.is_custom else "providers", {}).get(provider_id, {})
        api_key = override.api_key if override and "api_key" in override.model_fields_set else saved.get("api_key", "")
        base_url = override.base_url.strip() if override else item.base_url
        if item.require_api_key and not api_key: return TestConnectionResponse(success=False, message="请先配置 API Key")
        if not base_url: return TestConnectionResponse(success=False, message="请先配置 Base URL")
        start = time.perf_counter()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(override.custom_headers if override else item.custom_headers)
        normalized_base_url = base_url.rstrip("/")
        if item.id == "ollama" and not normalized_base_url.endswith("/v1"):
            models_url = normalized_base_url + "/api/tags"
        else:
            models_url = normalized_base_url + "/models"
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                response = await client.get(models_url, headers=headers)
            latency = int((time.perf_counter() - start) * 1000)
            success = 200 <= response.status_code < 300
            return TestConnectionResponse(success=success, message="连接成功" if success else f"上游返回 HTTP {response.status_code}", latency_ms=latency)
        except httpx.HTTPError as exc:
            return TestConnectionResponse(success=False, message=f"连接失败：{exc.__class__.__name__}")
