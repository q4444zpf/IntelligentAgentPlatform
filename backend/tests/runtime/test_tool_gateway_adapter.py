import pytest

from app.runtime.tool_gateway_adapter import ToolGatewayAdapter
from app.tools.schemas import ToolExecutionContext, ToolExecutionResult, ToolRuntimeError


class FakeGateway:
    def __init__(self):
        self.calls = []

    def execute(self, call, context, authorized_tool_ids):
        self.calls.append((call, context, authorized_tool_ids))
        return ToolExecutionResult("inv-1", {"ok": True})


def context():
    return ToolExecutionContext("u", "run-1", "conv", "p", "user", ("user",))


def test_adapter_converts_deep_agent_tool_call_to_platform_gateway_call():
    gateway = FakeGateway()
    tool = ToolGatewayAdapter(
        gateway=gateway,
        tool_id="forecast.run",
        description="运行预报",
        input_schema={"type": "object"},
        context=context(),
    )

    result = tool.invoke({"station": "A"})

    assert result == {"ok": True}
    call, execution_context, authorized = gateway.calls[0]
    assert call.name == "forecast.run"
    assert call.arguments == {"station": "A"}
    assert call.id.startswith("deepagents-")
    assert execution_context == context()
    assert authorized == {"forecast.run"}


def test_adapter_returns_safe_tool_error_without_raw_exception():
    class FailingGateway(FakeGateway):
        def execute(self, *_args):
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")

    tool = ToolGatewayAdapter(
        gateway=FailingGateway(), tool_id="secret.tool", description="", input_schema={}, context=context()
    )

    with pytest.raises(RuntimeError, match="该工具当前不可用"):
        tool.invoke({})
