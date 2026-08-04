import time
from copy import deepcopy

from datetime import UTC, datetime
from uuid import uuid4
import httpx

from sqlalchemy.orm import Session
from app.audit.management import management_event_id, management_trace_id
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext
from .registry import builtin_providers
from .schemas import ActiveModel, AddModelRequest, CreateProviderRequest, DiscoverModelsResponse, ModelConfigRequest, ModelInfo, ProbeMultimodalResponse, ProviderConfigRequest, ProviderInfo, TestConnectionResponse
from .store import ProviderStore


class ProviderService:
    def __init__(self, store: ProviderStore | None = None, *, audit_recorder: AuditRecorder | None = None):
        self.store = store or ProviderStore()
        self.audit_recorder = audit_recorder or AuditRecorder()

    def _commit_management(self, context: RequestContext, session: Session, request_id: str | None, *, action: str, resource_id: str, resource_name: str, risk_level: str = "high", metadata: dict | None = None) -> None:
        metadata = metadata or {}
        try:
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
                actor_role=context.actor_role, trace_id=management_trace_id(request_id), category="management", source="llm", action=action,
                status="succeeded", risk_level=risk_level, resource_type="model_provider",
                resource_id=resource_id, resource_name=resource_name,
                summary=f"Model provider resource {resource_id} management operation succeeded",
                metadata=metadata, allowed_metadata_keys=frozenset(metadata),
                idempotency_key=f"management:{management_event_id(request_id)}:succeeded:{action}:{resource_id}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise

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

    def configure(self, provider_id: str, body: ProviderConfigRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProviderInfo:
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
        if context is None or session is None:
            self.store.save(state)
            return self.get(provider_id)
        try:
            self.store.save_in_session(session, state)
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
                actor_role=context.actor_role, trace_id=management_trace_id(request_id), category="management", source="llm",
                action="resource.updated", status="succeeded", risk_level="high",
                resource_type="model_provider", resource_id=provider_id,
                resource_name=provider.name, summary=f"Model provider {provider_id} was updated",
                metadata={"enabled": bucket["enabled"], "protocol": bucket.get("protocol", provider.protocol)},
                allowed_metadata_keys=frozenset({"enabled", "protocol"}),
                idempotency_key=f"management:{management_event_id(request_id)}:succeeded:provider.configure:{provider_id}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        return self.get(provider_id)

    def create(self, body: CreateProviderRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProviderInfo:
        providers, state = self._merged()
        if body.id in providers: raise ValueError("Provider ID already exists")
        provider = ProviderInfo(id=body.id, name=body.name.strip(), kind="local", base_url=body.default_base_url.strip(), protocol=body.protocol, is_custom=True)
        state.setdefault("custom_providers", {})[body.id] = {"definition": provider.model_dump(exclude={"masked_api_key", "configured", "models"}), "api_key": "", "api_key_prefix": body.api_key_prefix, "models": []}
        if context is None or session is None:
            self.store.save(state)
        else:
            self.store.save_in_session(session, state)
            self._commit_management(context, session, request_id, action="resource.created", resource_id=body.id, resource_name=body.name)
        return self.get(body.id)

    def add_model(self, provider_id: str, body: AddModelRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProviderInfo:
        provider, state = self.get(provider_id), self.store.load()
        if any(item.id == body.id for item in provider.models): raise ValueError("Model ID already exists")
        model = ModelInfo(id=body.id, name=body.name or body.id, type=body.type, builtin=False)
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        key = "models" if provider.is_custom else "extra_models"
        bucket.setdefault(key, []).append(model.model_dump())
        if context is None or session is None: self.store.save(state)
        else:
            self.store.save_in_session(session, state)
            self._commit_management(context, session, request_id, action="resource.updated", resource_id=f"{provider_id}/{body.id}", resource_name=body.name or body.id, metadata={"model_id": body.id})
        return self.get(provider_id)

    def configure_model(self, provider_id: str, model_id: str, body: ModelConfigRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProviderInfo:
        provider, state = self.get(provider_id), self.store.load()
        if not any(item.id == model_id for item in provider.models): raise KeyError(model_id)
        bucket_name = "custom_providers" if provider.is_custom else "providers"
        bucket = state.setdefault(bucket_name, {}).setdefault(provider_id, {})
        bucket.setdefault("model_configs", {}).setdefault(model_id, {}).update(body.model_dump())
        if provider.is_custom:
            for item in bucket.get("models", []):
                if item["id"] == model_id: item.update(body.model_dump())
        if context is None or session is None: self.store.save(state)
        else:
            self.store.save_in_session(session, state)
            self._commit_management(context, session, request_id, action="resource.updated", resource_id=f"{provider_id}/{model_id}", resource_name=model_id, metadata={"model_id": model_id})
        return self.get(provider_id)

    def remove_model(self, provider_id: str, model_id: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProviderInfo:
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
        if context is None or session is None: self.store.save(state)
        else:
            self.store.save_in_session(session, state)
            self._commit_management(context, session, request_id, action="resource.deleted", resource_id=f"{provider_id}/{model_id}", resource_name=model_id)
        return self.get(provider_id)

    async def discover_models(self, provider_id: str, save: bool = True, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> DiscoverModelsResponse:
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
            if context is None or session is None: self.store.save(state)
            else:
                self.store.save_in_session(session, state)
                self._commit_management(context, session, request_id, action="resource.updated", resource_id=provider_id, resource_name=item.name, metadata={"discovered_count": len(additions)})
        return DiscoverModelsResponse(models=discovered, discovered_count=len(discovered), added_count=len(additions) if save else 0)

    async def probe_multimodal(self, provider_id: str, model_id: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ProbeMultimodalResponse:
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
            self._save_probe_result(state, item, model_id, result, context=context, session=session, request_id=request_id)
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
        self._save_probe_result(state, item, model_id, result, context=context, session=session, request_id=request_id)
        return result

    def _save_probe_result(self, state: dict, item: ProviderInfo, model_id: str, result: ProbeMultimodalResponse, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> None:
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
        if context is None or session is None: self.store.save(state)
        else:
            self.store.save_in_session(session, state)
            self._commit_management(context, session, request_id, action="resource.updated", resource_id=f"{item.id}/{model_id}", resource_name=model_id, metadata={"probe_source": "probed"})

    def get_active(self) -> ActiveModel:
        return ActiveModel.model_validate(self.store.load().get("active_model") or {})

    def set_active(self, active: ActiveModel, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> ActiveModel:
        provider = self.get(active.provider_id)
        if not provider.configured or not any(model.id == active.model and model.enabled for model in provider.models): raise ValueError("Provider or model is unavailable")
        state = self.store.load(); state["active_model"] = active.model_dump()
        if context is None or session is None: self.store.save(state)
        else:
            self.store.save_in_session(session, state); self._commit_management(context, session, request_id, action="resource.updated", resource_id=f"{active.provider_id}/{active.model}", resource_name=active.model, metadata={"active": True})
        return active

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
