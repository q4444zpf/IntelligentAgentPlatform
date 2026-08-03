from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ToolSource = Literal["builtin", "mcp", "knowledge", "artifact", "sandbox"]
ToolRisk = Literal["low", "medium", "high", "critical"]


class ToolInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tool_id: str
    version: str
    name: str
    description: str
    source: ToolSource
    risk_level: ToolRisk
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    requires_approval: bool
    published: bool
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    unit_id: str
    run_id: str
    conversation_id: str
    project_id: str
    user_id: str
    timezone: str = "Asia/Shanghai"


@dataclass(frozen=True)
class ToolExecutionResult:
    invocation_id: str
    value: dict[str, Any]


class ToolRuntimeError(Exception):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)
