from app.model_providers.schemas import AddModelRequest, CreateProviderRequest, ProviderConfigRequest
from app.model_providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.model_providers.store import ProviderStore
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def provider_store(path):
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    return ProviderStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def test_lists_builtin_and_persists_custom_provider(tmp_path):
    service = ProviderService(provider_store(tmp_path / "providers.db"))
    assert any(provider.id == "deepseek" for provider in service.list())
    provider_names = {provider.id: provider.name for provider in service.list()}
    assert {
        "gemini": "Google Gemini",
        "dashscope": "Aliyun",
        "moonshot": "Kimi",
        "minimax": "MiniMax",
        "mimo-tokenplan": "Xiaomi MiMo Token Plan",
        "volcengine": "Volcano Engine",
        "modelscope": "ModelScope",
        "siliconflow": "SiliconFlow",
        "azure-openai": "Azure OpenAI",
    }.items() <= provider_names.items()
    service.create(CreateProviderRequest(id="water-model", name="水利专用模型", default_base_url="http://localhost:9000/v1"))
    configured = service.configure("water-model", ProviderConfigRequest(name="水利专用模型", base_url="http://localhost:9000/v1", api_key="secret-key"))
    assert configured.configured is True
    assert configured.masked_api_key.endswith("-key")
    service.add_model("water-model", AddModelRequest(id="reservoir-agent", name="水库调度模型"))
    reloaded = ProviderService(provider_store(tmp_path / "providers.db")).get("water-model")
    assert reloaded.models[0].id == "reservoir-agent"


def test_disables_and_reenables_local_provider_without_api_key(tmp_path):
    database = tmp_path / "local-provider.db"
    service = ProviderService(provider_store(database))

    enabled = service.configure(
        "ollama",
        ProviderConfigRequest(
            base_url="http://127.0.0.1:11434/v1",
            enabled=True,
        ),
    )
    assert enabled.configured is True

    disabled = service.configure(
        "ollama",
        ProviderConfigRequest(
            base_url="http://127.0.0.1:11434/v1",
            enabled=False,
        ),
    )
    assert disabled.enabled is False
    assert disabled.configured is False

    reloaded = ProviderService(provider_store(database))
    assert reloaded.get("ollama").configured is False

    reenabled = reloaded.configure(
        "ollama",
        ProviderConfigRequest(
            base_url="http://127.0.0.1:11434/v1",
            enabled=True,
        ),
    )
    assert reenabled.enabled is True
    assert reenabled.configured is True


def test_ollama_connection_does_not_send_empty_authorization_header(tmp_path):
    captured: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["path"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"models": []}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = ProviderService(provider_store(tmp_path / "ollama-test.db"))
        result = asyncio.run(
            service.test(
                "ollama",
                override=ProviderConfigRequest(
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    enabled=True,
                ),
            )
        )
        assert result.success is True
        assert captured == {"path": "/api/tags", "authorization": None}
    finally:
        server.shutdown()
        server.server_close()


def test_discovers_and_removes_ollama_models(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"models":[{"name":"qwen2.5:7b","model":"qwen2.5:7b"}]}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = tmp_path / "ollama-discovery.db"
        service = ProviderService(provider_store(database))
        service.configure(
            "ollama",
            ProviderConfigRequest(
                base_url=f"http://127.0.0.1:{server.server_port}",
                enabled=True,
            ),
        )
        discovered = asyncio.run(service.discover_models("ollama"))
        assert discovered.discovered_count == 1
        assert discovered.added_count == 1
        assert service.get("ollama").models[0].id == "qwen2.5:7b"

        duplicate = asyncio.run(service.discover_models("ollama"))
        assert duplicate.added_count == 0

        removed = service.remove_model("ollama", "qwen2.5:7b")
        assert removed.models == []
        assert ProviderService(provider_store(database)).get("ollama").models == []
    finally:
        server.shutdown()
        server.server_close()


def test_rejects_removing_builtin_model(tmp_path):
    service = ProviderService(provider_store(tmp_path / "builtin.db"))
    try:
        service.remove_model("deepseek", "deepseek-chat")
        raise AssertionError("Expected built-in model removal to fail")
    except ValueError as error:
        assert "Built-in" in str(error)


def test_probes_and_persists_multimodal_capability(tmp_path):
    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            captured["path"] = self.path
            captured["payload"] = self.rfile.read(length).decode("utf-8")
            captured["authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"capabilities":["completion","vision"]}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = tmp_path / "multimodal.db"
        service = ProviderService(provider_store(database))
        service.configure("ollama", ProviderConfigRequest(base_url=f"http://127.0.0.1:{server.server_port}", enabled=True))
        service.add_model("ollama", AddModelRequest(id="vision-model", name="Vision Model"))
        result = asyncio.run(service.probe_multimodal("ollama", "vision-model"))
        assert result.supports_image is True
        assert result.supports_multimodal is True
        assert result.supports_video is False
        assert captured["path"] == "/api/show"
        assert captured["authorization"] is None
        assert "vision-model" in captured["payload"]

        persisted = next(model for model in ProviderService(provider_store(database)).get("ollama").models if model.id == "vision-model")
        assert persisted.supports_image is True
        assert persisted.supports_multimodal is True
        assert persisted.probe_source == "probed"
    finally:
        server.shutdown()
        server.server_close()

def test_configure_commits_provider_and_redacted_audit_together(tmp_path):
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    from app.core.request_context import RequestContext

    store = provider_store(tmp_path / "provider-audit.db")
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    service = ProviderService(store)
    with store.session_factory() as session:
        service.configure(
            "ollama",
            ProviderConfigRequest(base_url="http://127.0.0.1:11434/v1", api_key="top-secret-key", custom_headers={"Authorization": "Bearer hidden"}, enabled=True),
            context=context, session=session, request_id="provider-configure-1",
        )
        event = session.scalar(select(AuditEvent))
    serialized = f"{event.summary} {event.metadata_json}"
    assert event.action == "resource.updated"
    assert event.source == "llm"
    assert "top-secret-key" not in serialized
    assert "Bearer hidden" not in serialized
