from app.runtime.deepagents_factory import (
    DeepAgentFactory,
    PublishedAgentSnapshot,
    PublishedToolSnapshot,
)


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
    assert captured["metadata"] == {"agent_id": "agent-1"}


def test_factory_rejects_empty_agent_identifier():
    snapshot = PublishedAgentSnapshot("", "name", "prompt", "", ())

    try:
        DeepAgentFactory(creator=lambda **_: object()).build(snapshot, model="m")
    except ValueError as error:
        assert str(error) == "agent_id is required"
    else:
        raise AssertionError("expected invalid snapshot to fail")
