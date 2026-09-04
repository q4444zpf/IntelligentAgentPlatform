import json
from typing import TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, START, StateGraph

from app.runtime.deepagents_factory import (
    DeepAgentFactory,
    PublishedAgentSnapshot,
    PublishedToolSnapshot,
    create_in_memory_checkpointer,
)
from app.runtime.gateway_model import GatewayChatModel
from app.runtime.gateway_tools import RunnerApprovalInterruption
from app.runtime.langgraph_runtime import LangGraphRuntimeAdapter, RuntimeState


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
    output = graph.invoke(
        {"messages": [{"role": "user", "content": "execute"}]}
    )
    assert output["messages"][-1].content == "completed"


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


def test_factory_checkpoint_resumes_exact_node_after_graph_recreation():
    class WorkflowState(TypedDict, total=False):
        prepared: bool
        messages: list[dict]

    class Store:
        def __init__(self, state=None):
            self.state = state

        def load_latest(self, _run_id):
            return json.loads(json.dumps(self.state))

        def save(self, _run_id, _key, state):
            self.state = json.loads(json.dumps(state))

    prepared_calls = []
    approval = {"granted": False}

    def creator(*, checkpointer, **_kwargs):
        graph = StateGraph(WorkflowState)

        def prepare(_state):
            prepared_calls.append("prepare")
            return {"prepared": True}

        def await_approval(_state):
            if not approval["granted"]:
                raise RunnerApprovalInterruption("approval-1")
            return {"messages": [{"role": "assistant", "content": "approved"}]}

        graph.add_node("prepare", prepare)
        graph.add_node("approval", await_approval)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "approval")
        graph.add_edge("approval", END)
        return graph.compile(checkpointer=checkpointer)

    snapshot = PublishedAgentSnapshot(
        agent_id="agent-1",
        name="Agent",
        system_prompt="system",
        context_prompt="",
        tools=(),
    )
    store = Store()
    factory = DeepAgentFactory(creator=creator)

    with pytest.raises(RunnerApprovalInterruption):
        LangGraphRuntimeAdapter(
            factory.build(
                snapshot,
                model="model",
                tools=[],
                checkpointer=create_in_memory_checkpointer(),
            ),
            checkpoint_store=store,
        ).invoke(RuntimeState(run_id="run-1", messages=[]))

    approval["granted"] = True
    restarted_store = Store(json.loads(json.dumps(store.state)))
    result = LangGraphRuntimeAdapter(
        factory.build(
            snapshot,
            model="model",
            tools=[],
            checkpointer=create_in_memory_checkpointer(),
        ),
        checkpoint_store=restarted_store,
    ).invoke(RuntimeState(run_id="run-1", messages=[]))

    assert prepared_calls == ["prepare"]
    assert result.content == "approved"


def test_uncheckpointed_graph_does_not_mask_approval_interruption():
    class Graph:
        @staticmethod
        def invoke(_state, *, config=None):
            raise RunnerApprovalInterruption("approval-1")

        @staticmethod
        def get_state(_config):
            raise ValueError("No checkpointer set")

    class Store:
        @staticmethod
        def load_latest(_run_id):
            return None

        @staticmethod
        def save(_run_id, _key, _state):
            return None

    with pytest.raises(RunnerApprovalInterruption) as exc_info:
        LangGraphRuntimeAdapter(Graph(), checkpoint_store=Store()).invoke(
            RuntimeState(run_id="run-1", messages=[])
        )
    assert exc_info.value.approval_id == "approval-1"


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
