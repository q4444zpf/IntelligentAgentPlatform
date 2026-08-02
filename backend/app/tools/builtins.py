from typing import Any


BUILTIN_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "tool_id": "system.get_current_time", "version": "1.0.0", "name": "获取当前时间",
        "description": "获取指定 IANA 时区的当前日期、时间和星期，默认使用 Asia/Shanghai。",
        "source": "builtin", "risk_level": "low",
        "input_schema": {"type": "object", "properties": {"timezone": {"type": "string", "description": "IANA timezone name; defaults to Asia/Shanghai"}}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {"iso_datetime": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"}, "weekday": {"type": "string"}, "weekday_zh": {"type": "string"}, "timezone": {"type": "string"}},
            "required": ["iso_datetime", "date", "time", "weekday", "weekday_zh", "timezone"], "additionalProperties": False,
        },
        "requires_approval": False, "published": True, "enabled": True,
    },
    {
        "tool_id": "system.get_runtime_context", "version": "1.0.0", "name": "获取运行上下文",
        "description": "获取由平台验证的当前运行时间、时区、用户、项目、会话和运行标识。",
        "source": "builtin", "risk_level": "low",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {"current_time": {"type": "string"}, "timezone": {"type": "string"}, "user_id": {"type": "string"}, "project_id": {"type": "string"}, "conversation_id": {"type": "string"}, "run_id": {"type": "string"}},
            "required": ["current_time", "timezone", "user_id", "project_id", "conversation_id", "run_id"], "additionalProperties": False,
        },
        "requires_approval": False, "published": True, "enabled": True,
    },
)
