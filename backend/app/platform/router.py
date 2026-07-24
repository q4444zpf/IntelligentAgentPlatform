from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..model_providers.service import ProviderService


class PlatformOverview(BaseModel):
    status: str
    service: str
    version: str
    checked_at: datetime
    provider_count: int
    configured_provider_count: int
    model_count: int
    enabled_model_count: int
    active_provider_id: str
    active_model: str


router = APIRouter()
provider_service = ProviderService()


@router.get("/overview", response_model=PlatformOverview)
def get_overview() -> PlatformOverview:
    providers = provider_service.list()
    models = [model for provider in providers for model in provider.models]
    active = provider_service.get_active()
    return PlatformOverview(
        status="ok",
        service="intelligent-agent-platform-api",
        version="0.2.0",
        checked_at=datetime.now(UTC),
        provider_count=len(providers),
        configured_provider_count=sum(provider.configured for provider in providers),
        model_count=len(models),
        enabled_model_count=sum(model.enabled for model in models),
        active_provider_id=active.provider_id,
        active_model=active.model,
    )
