from fastapi.testclient import TestClient

from app.main import app
from app.model_providers.schemas import AddModelRequest, CreateProviderRequest, ProviderConfigRequest
from app.model_providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.model_providers.store import ProviderStore
from app.platform import router as platform_router


class _HealthyStorage:
    def __init__(self):
        self.client = self

    def list_buckets(self):
        return {"Buckets": []}


class _FailingStorage:
    def __init__(self):
        self.client = self

    def list_buckets(self):
        raise RuntimeError("secret endpoint and credentials must not leak")


def test_platform_overview_reports_provider_and_model_counts(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'overview.db'}")
    Base.metadata.create_all(engine)
    store = ProviderStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    service = ProviderService(store)
    service.create(CreateProviderRequest(id="water-model", name="水利专用模型", default_base_url="http://localhost:9000/v1"))
    service.configure("water-model", ProviderConfigRequest(base_url="http://localhost:9000/v1", api_key="secret"))
    service.add_model("water-model", AddModelRequest(id="water-chat", name="水利问答模型"))

    original = platform_router.provider_service
    platform_router.provider_service = service
    try:
        overview = platform_router.get_overview()
    finally:
        platform_router.provider_service = original

    assert overview.status == "ok"
    assert overview.provider_count >= 1
    assert overview.configured_provider_count >= 1
    assert overview.model_count >= 1
    assert overview.enabled_model_count >= 1


def test_platform_services_returns_five_records_in_stable_order(monkeypatch):
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false")
    monkeypatch.setattr(platform_router, "S3ObjectStorage", _HealthyStorage, raising=False)

    result = platform_router.get_services()

    assert [service.name for service in result.services] == [
        "API",
        "Workflow Runner",
        "PostgreSQL",
        "MinIO",
        "Sandbox Launcher",
    ]
    assert len(result.services) == 5


def test_platform_services_is_available_at_the_public_api_path(monkeypatch):
    monkeypatch.setattr(
        platform_router,
        "check_service_health",
        lambda _name: {"status": "healthy", "detail": "available"},
    )

    response = TestClient(app).get("/api/platform/services")

    assert response.status_code == 200
    assert [service["name"] for service in response.json()["services"]] == [
        "API",
        "Workflow Runner",
        "PostgreSQL",
        "MinIO",
        "Sandbox Launcher",
    ]


def test_minio_health_check_maps_success_to_safe_status(monkeypatch):
    monkeypatch.setattr(platform_router, "S3ObjectStorage", _HealthyStorage, raising=False)

    assert platform_router.check_service_health("MinIO") == {
        "status": "healthy",
        "detail": "available",
    }


def test_minio_health_check_maps_failure_without_exposing_exception(monkeypatch):
    monkeypatch.setattr(platform_router, "S3ObjectStorage", _FailingStorage, raising=False)

    result = platform_router.check_service_health("MinIO")

    assert result == {"status": "unhealthy", "detail": "unreachable"}
    assert "secret endpoint" not in str(result)


def test_sandbox_health_check_reports_disabled_without_contacting_runner(monkeypatch):
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("disabled sandbox must not call Workflow Runner")

    monkeypatch.setattr(platform_router, "urlopen", fail_if_called, raising=False)

    assert platform_router.check_service_health("Sandbox Launcher") == {
        "status": "disabled",
        "detail": "not enabled",
    }


def test_workflow_runner_health_uses_bounded_http_timeout(monkeypatch):
    calls = []

    class Response:
        def read(self):
            return b'{"status": "healthy", "sandbox": true}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_HEALTH_URL", "http://runner.internal/health")
    monkeypatch.setattr(platform_router, "urlopen", fake_urlopen, raising=False)

    assert platform_router.check_service_health("Workflow Runner") == {
        "status": "healthy",
        "detail": "available",
    }
    assert calls == [("http://runner.internal/health", 1.5)]


def test_postgresql_health_check_executes_select_one(monkeypatch):
    statements = []

    class Session:
        def execute(self, statement):
            statements.append(str(statement))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(platform_router, "SessionFactory", Session, raising=False)

    assert platform_router.check_service_health("PostgreSQL") == {
        "status": "healthy",
        "detail": "available",
    }
    assert statements == ["SELECT 1"]


def test_sandbox_health_check_reads_enabled_readiness_from_workflow_runner(monkeypatch):
    class Response:
        def read(self):
            return b'{"status": "healthy", "sandbox": true}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_HEALTH_URL", "http://runner.internal/health")
    monkeypatch.setattr(platform_router, "urlopen", lambda *_args, **_kwargs: Response(), raising=False)

    assert platform_router.check_service_health("Sandbox Launcher") == {
        "status": "healthy",
        "detail": "available",
    }


def test_workflow_runner_failure_returns_a_safe_status(monkeypatch):
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_HEALTH_URL", "http://runner.internal/secret")

    def fail_with_sensitive_detail(*_args, **_kwargs):
        raise RuntimeError("credentials at http://runner.internal/secret must not leak")

    monkeypatch.setattr(platform_router, "urlopen", fail_with_sensitive_detail, raising=False)

    result = platform_router.check_service_health("Workflow Runner")

    assert result == {"status": "unhealthy", "detail": "unreachable"}
    assert "runner.internal" not in str(result)
    assert "credentials" not in str(result)
