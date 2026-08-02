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
