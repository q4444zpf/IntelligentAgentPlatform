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
    ModelResult,
    ModelSelection,
    ModelUpstreamError,
    OpenAICompatibleModelGateway,
)
from app.tools.schemas import ToolCall, ToolDefinition


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


def _configured_gateway(tmp_path, monkeypatch, response_data):
    store = provider_store(tmp_path / "providers.db")
    service = ProviderService(store)
    service.configure("deepseek", ProviderConfigRequest(
        base_url="https://api.deepseek.com/v1", api_key="runtime-secret"))
    service.set_active(ActiveModel(provider_id="deepseek", model="deepseek-chat"))
    captured = {}

    class Response:
        def raise_for_status(self): pass
        def json(self): return response_data

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def post(self, _url, *, json, **_kwargs):
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(model_gateway.httpx, "Client", Client)
    return OpenAICompatibleModelGateway(store), captured


def test_omits_tool_fields_when_no_tools_are_supplied(tmp_path, monkeypatch):
    gateway, captured = _configured_gateway(
        tmp_path, monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    gateway.generate([{"role": "user", "content": "hello"}])
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]


def test_serializes_tools_as_openai_functions_with_auto_choice(tmp_path, monkeypatch):
    gateway, captured = _configured_gateway(
        tmp_path, monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    tools = [ToolDefinition("system.get_current_time", "Get trusted current time",
                            {"type": "object", "properties": {}})]
    gateway.generate([{"role": "user", "content": "time?"}], tools=tools)
    assert captured["payload"]["tools"] == [{"type": "function", "function": {
        "name": "system.get_current_time", "description": "Get trusted current time",
        "parameters": {"type": "object", "properties": {}}}}]
    assert captured["payload"]["tool_choice"] == "auto"


def test_parses_single_tool_call(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        {
            "choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call-time",
                "type": "function",
                "function": {
                    "name": "system.get_current_time",
                    "arguments": "{}",
                },
            }]}}]
        },
    )

    result = gateway.generate([{"role": "user", "content": "time?"}])

    assert result.tool_calls == (
        ToolCall("call-time", "system.get_current_time", {}),
    )

def test_parses_multiple_tool_calls_and_preserves_ids(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(tmp_path, monkeypatch, {"choices": [{"message": {
        "content": None, "tool_calls": [
            {"id": "call-time", "type": "function", "function": {
                "name": "system.get_current_time", "arguments": "{}"}},
            {"id": "call-context", "type": "function", "function": {
                "name": "system.get_runtime_context", "arguments": '{"include_time": true}'}}
        ]}}]})
    result = gateway.generate([{"role": "user", "content": "context"}])
    assert result == ModelResult(content=None, tool_calls=(
        ToolCall("call-time", "system.get_current_time", {}),
        ToolCall("call-context", "system.get_runtime_context", {"include_time": True})))


@pytest.mark.parametrize("arguments", ["not-json", "[]", "null", "1"])
def test_rejects_tool_call_arguments_that_are_not_json_objects(
    tmp_path, monkeypatch, arguments
):
    gateway, _ = _configured_gateway(tmp_path, monkeypatch, {
        "secret": "response-secret", "choices": [{"message": {"content": "fallback",
        "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "system.get_current_time", "arguments": arguments}}]}}]})
    with pytest.raises(ModelUpstreamError) as captured:
        gateway.generate([{"role": "user", "content": "time?"}])
    assert str(captured.value) == "The model request failed"
    assert "response-secret" not in str(captured.value)
    assert "runtime-secret" not in str(captured.value)


def test_rejects_empty_content_without_tool_calls(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path, monkeypatch, {"choices": [{"message": {"content": None}}]})
    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "hello"}])


def test_accepts_empty_content_with_tool_calls_and_normalizes_it(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "call-time",
            "type": "function",
            "function": {
                "name": "system.get_current_time",
                "arguments": "{}",
            },
        }]}}]},
    )

    result = gateway.generate([{"role": "user", "content": "time?"}])

    assert result.content is None
    assert result.tool_calls == (
        ToolCall("call-time", "system.get_current_time", {}),
    )


def test_rejects_empty_string_content_without_tool_calls(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path, monkeypatch, {"choices": [{"message": {"content": ""}}]}
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "hello"}])


def test_preserves_tool_role_message_and_tool_call_id(tmp_path, monkeypatch):
    gateway, captured = _configured_gateway(
        tmp_path, monkeypatch, {"choices": [{"message": {"content": "done"}}]})
    messages = [
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": "call-time", "content": '{"time":"12:00"}'},
    ]
    gateway.generate(messages)
    assert captured["payload"]["messages"][1:] == messages


def _tool_call(call_id="call-1", name="system.get_current_time", arguments="{}"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _tool_call_response(tool_calls, *, content=None, usage=None):
    response = {
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}]
    }
    if usage is not None:
        response["usage"] = usage
    return response


def test_rejects_more_than_maximum_tool_calls(tmp_path, monkeypatch):
    calls = [_tool_call(call_id=f"call-{index}") for index in range(9)]
    gateway, _ = _configured_gateway(
        tmp_path, monkeypatch, _tool_call_response(calls)
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "time?"}])


def test_accepts_tool_arguments_at_utf8_byte_limit(tmp_path, monkeypatch):
    prefix = '{"value":"'
    suffix = '"}'
    arguments = prefix + "a" * (
        model_gateway.MAX_TOOL_ARGUMENT_BYTES - len(prefix) - len(suffix)
    ) + suffix
    assert len(arguments.encode("utf-8")) == model_gateway.MAX_TOOL_ARGUMENT_BYTES
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call(arguments=arguments)]),
    )

    result = gateway.generate([{"role": "user", "content": "bounded"}])

    assert len(result.tool_calls[0].arguments["value"]) > 0


def test_rejects_tool_arguments_over_utf8_byte_limit(tmp_path, monkeypatch):
    prefix = '{"value":"'
    suffix = '"}'
    arguments = prefix + "a" * (
        model_gateway.MAX_TOOL_ARGUMENT_BYTES - len(prefix) - len(suffix) + 1
    ) + suffix
    assert len(arguments.encode("utf-8")) == model_gateway.MAX_TOOL_ARGUMENT_BYTES + 1
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call(arguments=arguments)]),
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "oversized"}])


def test_accepts_tool_arguments_at_json_depth_limit(tmp_path, monkeypatch):
    arguments = '{"nested":' * model_gateway.MAX_TOOL_ARGUMENT_DEPTH
    arguments += "0" + "}" * model_gateway.MAX_TOOL_ARGUMENT_DEPTH
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call(arguments=arguments)]),
    )

    result = gateway.generate([{"role": "user", "content": "nested"}])

    assert result.tool_calls[0].id == "call-1"


def test_rejects_tool_arguments_over_json_depth_limit(tmp_path, monkeypatch):
    depth = model_gateway.MAX_TOOL_ARGUMENT_DEPTH + 1
    arguments = '{"nested":' * depth + "0" + "}" * depth
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call(arguments=arguments)]),
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "too deep"}])


@pytest.mark.parametrize(
    "response_data",
    [
        {"choices": [{"message": None}]},
        {"choices": [{"message": []}]},
        _tool_call_response([None]),
        _tool_call_response([[]]),
        _tool_call_response([], content="ok", usage=[]),
    ],
)
def test_rejects_invalid_upstream_structures_safely(
    tmp_path, monkeypatch, response_data
):
    response_data["secret"] = "response-secret"
    gateway, _ = _configured_gateway(tmp_path, monkeypatch, response_data)

    with pytest.raises(ModelUpstreamError) as captured:
        gateway.generate([{"role": "user", "content": "hello"}])

    assert str(captured.value) == "The model request failed"
    assert "response-secret" not in str(captured.value)


def test_wraps_json_parser_recursion_error_safely(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call()]),
    )

    def raise_recursion_error(_value):
        raise RecursionError("response-secret")

    monkeypatch.setattr(model_gateway.json, "loads", raise_recursion_error)

    with pytest.raises(ModelUpstreamError) as captured:
        gateway.generate([{"role": "user", "content": "nested"}])

    assert str(captured.value) == "The model request failed"
    assert "response-secret" not in str(captured.value)

def test_rejects_duplicate_tool_call_ids(tmp_path, monkeypatch):
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([
            _tool_call(call_id="duplicate"),
            _tool_call(call_id="duplicate"),
        ]),
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "time?"}])


@pytest.mark.parametrize(
    ("call_id", "name"),
    [("", "system.get_current_time"), ("   ", "system.get_current_time"),
     ("call-1", ""), ("call-1", "\t")],
)
def test_rejects_blank_tool_call_id_or_name(tmp_path, monkeypatch, call_id, name):
    gateway, _ = _configured_gateway(
        tmp_path,
        monkeypatch,
        _tool_call_response([_tool_call(call_id=call_id, name=name)]),
    )

    with pytest.raises(ModelUpstreamError, match="The model request failed"):
        gateway.generate([{"role": "user", "content": "time?"}])