from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ModelInfo(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    type: str = "文本"
    enabled: bool = True
    builtin: bool = False
    max_tokens: int = Field(default=8192, ge=1, le=1_000_000)
    context_window: int = Field(default=128000, ge=1000, le=10_000_000)
    forward_reasoning: bool = True
    extra_config: dict[str, Any] = Field(default_factory=dict)
    supports_image: bool | None = None
    supports_video: bool | None = None
    supports_multimodal: bool | None = None
    probe_source: str | None = None


class ProviderInfo(BaseModel):
    id: str
    name: str
    kind: Literal["cloud", "local"] = "cloud"
    base_url: str = ""
    masked_api_key: str = ""
    require_api_key: bool = True
    protocol: str = "OpenAIChatModel"
    freeze_url: bool = False
    support_connection_check: bool = True
    support_model_discovery: bool = True
    api_key_prefixes: list[str] = Field(default_factory=list)
    generate_kwargs: dict[str, Any] = Field(default_factory=dict)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    auth_mode: Literal["api_key", "auth_token"] = "api_key"
    configured: bool = False
    enabled: bool = True
    is_custom: bool = False
    is_free_tier: bool = False
    provider_group: str | None = None
    provider_variant: str | None = None
    models: list[ModelInfo] = Field(default_factory=list)


class ProviderConfigRequest(BaseModel):
    name: str | None = None
    base_url: str
    api_key: str | None = None
    protocol: str | None = None
    generate_kwargs: dict[str, Any] = Field(default_factory=dict)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    auth_mode: Literal["api_key", "auth_token"] = "api_key"
    enabled: bool | None = None


class CreateProviderRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=60)
    default_base_url: str = ""
    api_key_prefix: str = Field(default="", max_length=24)
    protocol: str = "OpenAIChatModel"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        import re
        value = value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
            raise ValueError("ID must start with a lowercase letter and contain only a-z, 0-9, - or _")
        return value


class AddModelRequest(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    name: str | None = None
    type: str = "文本"


class ModelConfigRequest(BaseModel):
    max_tokens: int = Field(ge=1, le=1_000_000)
    context_window: int = Field(ge=1000, le=10_000_000)
    forward_reasoning: bool = True
    extra_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ActiveModel(BaseModel):
    provider_id: str = ""
    model: str = ""


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    latency_ms: int | None = None


class DiscoverModelsResponse(BaseModel):
    models: list[ModelInfo]
    discovered_count: int
    added_count: int


class ProbeMultimodalResponse(BaseModel):
    supports_image: bool = False
    supports_video: bool = False
    supports_multimodal: bool = False
    image_message: str = ""
    video_message: str = ""
