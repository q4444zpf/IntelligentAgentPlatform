import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.runtime.model_gateway as model_gateway
from app.db.base import Base
from app.model_providers.schemas import (
    ActiveModel,
    ModelConfigRequest,
    ProviderConfigRequest,
)
from app.model_providers.service import ProviderService
from app.model_providers.store import ProviderStore
from app.runtime.model_gateway import (
    ModelConfigurationError,
    ModelSelection,
    OpenAICompatibleModelGateway,
)


def provider_store(path) -> ProviderStore:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return ProviderStore(factory)


def test_builds_authoritative_runtime_model_identity_message():
    messages = model_gateway.build_runtime_messages(
        "deepseek",
        "deepseek-chat",
        [{"role": "user", "content": "identify yourself"}],
    )

    assert messages == [
        {
            "role": "system",
            "content": "Runtime model identity (authoritative platform configuration): provider_id=deepseek, model=deepseek-chat. When asked about model identity, use exactly this configuration and do not guess or claim another model.",
        },
        {"role": "user", "content": "identify yourself"},
    ]

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
                generate_kwargs={
                    "temperature": 0.2,
                    "model": "attacker-model",
                    "messages": [],
                    "stream": True,
                },
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
        assert captured["payload"]["messages"] == model_gateway.build_runtime_messages(
            "deepseek",
            "deepseek-chat",
            [{"role": "user", "content": "分析洪峰"}],
        )
        assert captured["payload"]["stream"] is False
        assert captured["payload"]["temperature"] == 0.2
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


def test_explicit_selection_overrides_active_model(tmp_path, monkeypatch):
    store = provider_store(tmp_path / "providers.db")
    service = ProviderService(store)
    service.configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
        ),
    )
    service.set_active(ActiveModel(provider_id="deepseek", model="deepseek-reasoner"))
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, headers, json):
            captured["headers"] = headers
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(model_gateway.httpx, "Client", Client)
    OpenAICompatibleModelGateway(store).generate(
        [{"role": "user", "content": "identify yourself"}],
        ModelSelection("deepseek", "deepseek-chat"),
    )

    assert captured["payload"]["model"] == "deepseek-chat"
    assert captured["payload"]["messages"] == model_gateway.build_runtime_messages(
        "deepseek",
        "deepseek-chat",
        [{"role": "user", "content": "identify yourself"}],
    )
    assert "runtime-secret" not in str(captured["payload"])


def test_partial_explicit_selection_falls_back_to_active_model(
    tmp_path, monkeypatch
):
    store = provider_store(tmp_path / "providers.db")
    service = ProviderService(store)
    service.configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
        ),
    )
    service.set_active(ActiveModel(provider_id="deepseek", model="deepseek-chat"))
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, json, **_kwargs):
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(model_gateway.httpx, "Client", Client)
    OpenAICompatibleModelGateway(store).generate(
        [{"role": "user", "content": "hello"}],
        ModelSelection("deepseek", ""),
    )

    assert captured["payload"]["model"] == "deepseek-chat"


def test_rejects_invalid_explicit_selection_without_exposing_secret(tmp_path):
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
            [{"role": "user", "content": "hello"}],
            ModelSelection("deepseek", "missing-model"),
        )

    assert "runtime-secret" not in str(captured.value)


def test_rejects_explicit_selection_when_provider_is_disabled(tmp_path):
    store = provider_store(tmp_path / "providers.db")
    ProviderService(store).configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
            enabled=False,
        ),
    )

    with pytest.raises(ModelConfigurationError) as captured:
        OpenAICompatibleModelGateway(store).generate(
            [{"role": "user", "content": "hello"}],
            ModelSelection("deepseek", "deepseek-chat"),
        )

    assert "runtime-secret" not in str(captured.value)


def test_rejects_explicit_selection_when_model_is_disabled(tmp_path):
    store = provider_store(tmp_path / "providers.db")
    service = ProviderService(store)
    service.configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
        ),
    )
    service.configure_model(
        "deepseek",
        "deepseek-chat",
        ModelConfigRequest(
            max_tokens=8192,
            context_window=128000,
            enabled=False,
        ),
    )

    with pytest.raises(ModelConfigurationError) as captured:
        OpenAICompatibleModelGateway(store).generate(
            [{"role": "user", "content": "hello"}],
            ModelSelection("deepseek", "deepseek-chat"),
        )

    assert "runtime-secret" not in str(captured.value)


def test_rejects_explicit_selection_with_unsupported_protocol(tmp_path):
    store = provider_store(tmp_path / "providers.db")
    ProviderService(store).configure(
        "deepseek",
        ProviderConfigRequest(
            base_url="https://api.deepseek.com/v1",
            api_key="runtime-secret",
            protocol="AnthropicChatModel",
        ),
    )

    with pytest.raises(ModelConfigurationError) as captured:
        OpenAICompatibleModelGateway(store).generate(
            [{"role": "user", "content": "hello"}],
            ModelSelection("deepseek", "deepseek-chat"),
        )

    assert "runtime-secret" not in str(captured.value)
