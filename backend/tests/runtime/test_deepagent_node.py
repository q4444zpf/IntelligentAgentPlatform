from app.runtime.deepagent_node import DeepAgentNode


class FakeAgent:
    def invoke(self, state, *, config=None):
        assert state["messages"][-1]["role"] == "user"
        return {"messages": [*state["messages"], {"role": "assistant", "content": "节点完成"}]}


def test_node_invokes_agent_and_marks_graph_state_completed():
    node = DeepAgentNode(FakeAgent())

    result = node({"run_id": "run-1", "messages": [{"role": "user", "content": "执行"}]}, {"configurable": {"thread_id": "run-1"}})

    assert result["status"] == "completed"
    assert result["messages"][-1]["content"] == "节点完成"
