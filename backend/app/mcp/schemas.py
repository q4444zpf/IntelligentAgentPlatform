from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


McpTransport = Literal["stdio", "streamable_http", "sse"]


class McpClientConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    transport: McpTransport = "streamable_http"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    credential_id: str | None = Field(default=None, max_length=128)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    enabled: bool = True

    @model_validator(mode="after")
    def validate_transport(self):
        if self.transport in {"streamable_http", "sse"}:
            if not self.url:
                raise ValueError("URL is required for remote MCP transports")
            if not self.url.startswith(("http://", "https://")):
                raise ValueError("URL must use http or https")
        elif not self.command:
            raise ValueError("command is required for stdio transport")
        return self


class McpClientCreate(McpClientConfig):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class McpClientInfo(McpClientConfig):
    key: str
    client_id: str
    status: str = "active"
    health_status: str = "not_checked"
    tools: list[str] | None = None
    tool_count: int = 0
    enabled_tool_count: int = 0
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class McpToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class McpToolWhitelistRequest(BaseModel):
    tools: list[str] | None = None


class McpProjectGrantRequest(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


class McpProjectGrantInfo(BaseModel):
    project_ids: list[str]


class McpOperationInfo(BaseModel):
    id: str
    client_id: str
    operation_type: str
    status: str
    phase: str
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class McpHealthInfo(BaseModel):
    health_status: str
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_latency_ms: int | None = None
    failure_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
