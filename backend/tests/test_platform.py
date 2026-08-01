from app.model_providers.schemas import AddModelRequest, CreateProviderRequest, ProviderConfigRequest
from app.model_providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.model_providers.store import ProviderStore
from app.platform import router as platform_router


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
