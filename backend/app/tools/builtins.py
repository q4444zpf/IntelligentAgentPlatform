from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schemas import ToolExecutionContext, ToolRuntimeError

Clock = Callable[[], datetime]
BuiltinExecutor = Callable[[dict[str, Any], ToolExecutionContext, Clock], dict[str, Any]]

_WEEKDAYS_EN = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
_WEEKDAYS_ZH = (
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
)


def _now_in_timezone(timezone_name: str, clock: Clock) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ToolRuntimeError("tool_invalid_arguments", "工具参数无效。") from error
    current = clock()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(zone)


def get_current_time(arguments: dict[str, Any], context: ToolExecutionContext, clock: Clock) -> dict[str, Any]:
    timezone_name = arguments.get("timezone", context.timezone)
    current = _now_in_timezone(timezone_name, clock)
    return {
        "iso_datetime": current.isoformat(timespec="seconds"),
        "date": current.date().isoformat(),
        "time": current.time().replace(tzinfo=None).isoformat(timespec="seconds"),
        "weekday": _WEEKDAYS_EN[current.weekday()],
        "weekday_zh": _WEEKDAYS_ZH[current.weekday()],
        "timezone": timezone_name,
    }


def get_runtime_context(arguments: dict[str, Any], context: ToolExecutionContext, clock: Clock) -> dict[str, Any]:
    current = _now_in_timezone(context.timezone, clock)
    return {
        "current_time": current.isoformat(timespec="seconds"),
        "timezone": context.timezone,
        "user_id": context.user_id,
        "project_id": context.project_id,
        "conversation_id": context.conversation_id,
        "run_id": context.run_id,
    }


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

BUILTIN_EXECUTORS: dict[str, BuiltinExecutor] = {
    "system.get_current_time": get_current_time,
    "system.get_runtime_context": get_runtime_context,
}
