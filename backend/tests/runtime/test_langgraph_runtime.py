from app.runtime.langgraph_runtime import LangGraphRuntimeAdapter, RuntimeState


class FakeGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, state, *, config=None):
        self.calls.append((state, config))
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": "LangGraph 运行完成"},
            ],
            "status": "completed",
        }


def test_runtime_adapter_invokes_graph_with_run_scoped_state_and_returns_final_message():
    graph = FakeGraph()
    adapter = LangGraphRuntimeAdapter(graph)

    result = adapter.invoke(
        RuntimeState(run_id="run-1", messages=[{"role": "user", "content": "执行"}]),
        metadata={"project_id": "p1"},
    )

    assert result.status == "completed"
    assert result.content == "LangGraph 运行完成"
    assert graph.calls[0][0]["run_id"] == "run-1"
    assert graph.calls[0][1]["configurable"]["thread_id"] == "run-1"
    assert graph.calls[0][1]["metadata"] == {"project_id": "p1"}


def test_runtime_adapter_rejects_graph_without_assistant_result():
    class EmptyGraph:
        def invoke(self, state, *, config=None):
            return {**state, "messages": state["messages"]}

    adapter = LangGraphRuntimeAdapter(EmptyGraph())

    try:
        adapter.invoke(RuntimeState(run_id="run-2", messages=[]))
    except RuntimeError as error:
        assert str(error) == "Graph did not produce an assistant result"
    else:
        raise AssertionError("expected missing assistant result to fail")


def test_runtime_adapter_restores_and_saves_checkpoint_state():
    class Checkpoints:
        def __init__(self):
            self.saved = []

        def load_latest(self, run_id):
            return {"run_id": run_id, "messages": [{"role": "assistant", "content": "恢复"}], "status": "waiting_approval"}

        def save(self, run_id, checkpoint_key, state):
            self.saved.append((run_id, checkpoint_key, state))

    class Graph:
        def invoke(self, state, *, config=None):
            assert state["messages"][-1]["content"] == "恢复"
            return {**state, "status": "completed"}

    checkpoints = Checkpoints()
    result = LangGraphRuntimeAdapter(Graph(), checkpoint_store=checkpoints).invoke(
        RuntimeState(run_id="run-3", messages=[])
    )

    assert result.status == "completed"
    assert checkpoints.saved[0][0:2] == ("run-3", "langgraph")
