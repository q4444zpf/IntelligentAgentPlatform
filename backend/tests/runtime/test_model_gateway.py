import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.model_providers.schemas import ActiveModel, ProviderConfigRequest
from app.model_providers.service import ProviderService
from app.model_providers.store import ProviderStore
from app.runtime.model_gateway import (
    ModelConfigurationError,
    OpenAICompatibleModelGateway,
)


def provider_store(path) -> ProviderStore:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return ProviderStore(factory)


def test_calls_active_openai_compatible_model_and_normalizes_usage(tmp_path):
    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            captured["path"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            captured["payload"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "choices": [{"message": {"content": "研判完成"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            }).encode())

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = provider_store(tmp_path / "providers.db")
        service = ProviderService(store)
        service.configure(
            "deepseek",
            ProviderConfigRequest(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="runtime-secret",
            ),
        )
        service.set_active(
            ActiveModel(provider_id="deepseek", model="deepseek-chat")
        )

        result = OpenAICompatibleModelGateway(store).generate(
            [{"role": "user", "content": "分析洪峰"}]
        )

        assert result.content == "研判完成"
        assert result.total_tokens == 18
        assert captured["path"] == "/v1/chat/completions"
        assert captured["authorization"] == "Bearer runtime-secret"
        assert captured["payload"]["model"] == "deepseek-chat"
    finally:
        server.shutdown()
        server.server_close()


def test_rejects_missing_active_model_without_exposing_provider_secret(tmp_path):
    store = provider_store(tmp_path / "providers.db")
    ProviderService(store).configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
        ),
    )

    with pytest.raises(ModelConfigurationError) as captured:
        OpenAICompatibleModelGateway(store).generate(
            [{"role": "user", "content": "分析洪峰"}]
        )

    assert "runtime-secret" not in str(captured.value)
