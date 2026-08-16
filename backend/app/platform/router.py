import json
import os
from datetime import UTC, datetime
from typing import Literal
from urllib.request import Request, urlopen

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from ..artifacts.storage import S3ObjectStorage
from ..core.database import SessionFactory
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


class ServiceStatus(BaseModel):
    name: str
    status: Literal["healthy", "unhealthy", "disabled"]
    detail: str


class PlatformServices(BaseModel):
    checked_at: datetime
    services: list[ServiceStatus]


router = APIRouter()
provider_service = ProviderService()

_SERVICE_NAMES = (
    "API",
    "Workflow Runner",
    "PostgreSQL",
    "MinIO",
    "Sandbox Launcher",
)
_AVAILABLE = {"status": "healthy", "detail": "available"}
_UNREACHABLE = {"status": "unhealthy", "detail": "unreachable"}
_DISABLED = {"status": "disabled", "detail": "not enabled"}


def _workflow_runner_health() -> dict[str, object] | None:
    url = os.getenv("IAP_WORKFLOW_RUNNER_HEALTH_URL", "").strip()
    if not url:
        return None
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=1.5) as response:
            health = json.loads(response.read().decode("utf-8"))
        return health if isinstance(health, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _sandbox_enabled() -> bool:
    return os.getenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def check_service_health(name: str) -> dict[str, str]:
    if name == "API":
        return _AVAILABLE
    if name == "Workflow Runner":
        health = _workflow_runner_health()
        return _AVAILABLE if health and health.get("status") == "healthy" else _UNREACHABLE
    if name == "PostgreSQL":
        try:
            with SessionFactory() as session:
                session.execute(text("SELECT 1"))
            return _AVAILABLE
        except Exception:  # noqa: BLE001
            return _UNREACHABLE
    if name == "MinIO":
        try:
            S3ObjectStorage().client.list_buckets()
            return _AVAILABLE
        except Exception:  # noqa: BLE001
            return _UNREACHABLE
    if name == "Sandbox Launcher":
        if not _sandbox_enabled():
            return _DISABLED
        health = _workflow_runner_health()
        return (
            _AVAILABLE
            if health and health.get("status") == "healthy" and health.get("sandbox") is True
            else _UNREACHABLE
        )
    return _UNREACHABLE


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


@router.get("/services", response_model=PlatformServices)
def get_services() -> PlatformServices:
    return PlatformServices(
        checked_at=datetime.now(UTC),
        services=[
            ServiceStatus(name=name, **check_service_health(name))
            for name in _SERVICE_NAMES
        ],
    )
