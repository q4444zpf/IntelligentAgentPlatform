from app.runtime.deepagents_factory import (
    DeepAgentFactory,
    PublishedAgentSnapshot,
    PublishedToolSnapshot,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from app.runtime.langgraph_runtime import LangGraphRuntimeAdapter, RuntimeState
from app.runtime.gateway_model import GatewayChatModel


class ToolBindingFakeChatModel(FakeListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


class FakeGatewayModelTransport:
    def __init__(self):
        self.calls = []

    def invoke_model(self, request, idempotency_key):
        self.calls.append((request, idempotency_key))
        return {
            "content": "completed",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "tool_calls": [],
        }


def test_factory_builds_deep_agent_from_published_snapshot_and_filters_tools():
    captured = {}

    def creator(**kwargs):
        captured.update(kwargs)
        return "deep-agent"

    snapshot = PublishedAgentSnapshot(
        agent_id="agent-1",
        name="洪水研判",
        system_prompt="你是研判助手",
        context_prompt="结合项目上下文",
        tools=(
            PublishedToolSnapshot("forecast", "预报", {"type": "object"}, published=True, enabled=True),
            PublishedToolSnapshot("disabled", "禁用工具", {"type": "object"}, published=True, enabled=False),
            PublishedToolSnapshot("draft", "草稿工具", {"type": "object"}, published=False, enabled=True),
        ),
    )

    agent = DeepAgentFactory(creator=creator).build(snapshot, model="model-ref")

    assert agent == "deep-agent"
    assert captured["model"] == "model-ref"
    assert captured["system_prompt"] == "你是研判助手\n\n结合项目上下文"
    assert [tool.name for tool in captured["tools"]] == ["forecast"]
    assert captured["name"] == "agent-1"
    assert "metadata" not in captured


def test_default_factory_builds_with_installed_deepagents_api():
    snapshot = PublishedAgentSnapshot(
        agent_id="agent-1",
        name="Agent",
        system_prompt="system",
        context_prompt="",
        tools=(),
    )

    graph = DeepAgentFactory().build(
        snapshot,
        model=ToolBindingFakeChatModel(responses=["completed"]),
        tools=[],
    )

    assert callable(graph.invoke)


def test_runtime_adapter_reads_installed_deepagents_message_output():
    class Checkpoints:
        def __init__(self):
            self.saved = []

        def load_latest(self, run_id):
            return None

        def save(self, run_id, checkpoint_key, state):
            assert all(isinstance(message, dict) for message in state["messages"])
            self.saved.append((run_id, checkpoint_key, state))

    snapshot = PublishedAgentSnapshot(
        agent_id="agent-1",
        name="Agent",
        system_prompt="system",
        context_prompt="",
        tools=(),
    )
    graph = DeepAgentFactory().build(
        snapshot,
        model=ToolBindingFakeChatModel(responses=["completed"]),
        tools=[],
    )

    checkpoints = Checkpoints()
    result = LangGraphRuntimeAdapter(graph, checkpoint_store=checkpoints).invoke(
        RuntimeState(
            run_id="run-1",
            messages=[{"role": "user", "content": "execute"}],
            status="running",
        )
    )

    assert result.status == "completed"
    assert result.content == "completed"
    assert checkpoints.saved[0][0:2] == ("run-1", "langgraph")


def test_installed_deepagents_invokes_gateway_chat_model():
    snapshot = PublishedAgentSnapshot(
        agent_id="agent-1",
        name="Agent",
        system_prompt="system",
        context_prompt="",
        tools=(),
    )
    transport = FakeGatewayModelTransport()
    graph = DeepAgentFactory().build(
        snapshot,
        model=GatewayChatModel(transport),
        tools=[],
    )

    result = LangGraphRuntimeAdapter(graph).invoke(
        RuntimeState(
            run_id="run-1",
            messages=[{"role": "user", "content": "execute"}],
            status="running",
        )
    )

    assert result.content == "completed"
    assert transport.calls[0][1] == "model-0"


def test_factory_rejects_empty_agent_identifier():
    snapshot = PublishedAgentSnapshot("", "name", "prompt", "", ())

    try:
        DeepAgentFactory(creator=lambda **_: object()).build(snapshot, model="m")
    except ValueError as error:
        assert str(error) == "agent_id is required"
    else:
        raise AssertionError("expected invalid snapshot to fail")
