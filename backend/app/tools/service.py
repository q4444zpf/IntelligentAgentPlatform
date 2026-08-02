import re

from .builtins import BUILTIN_TOOL_DEFINITIONS
from .schemas import ToolInfo
from .store import ToolStore

TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class ToolNotFoundError(Exception):
    pass


class ToolValidationError(Exception):
    pass


class ToolService:
    def __init__(self, store: ToolStore | None = None):
        self.store = store or ToolStore()
        self._ensure_builtins()

    def _ensure_builtins(self) -> None:
        for definition in BUILTIN_TOOL_DEFINITIONS:
            self.store.upsert_builtin(definition)

    @staticmethod
    def _validate_tool_id(tool_id: str) -> None:
        if not TOOL_ID_PATTERN.fullmatch(tool_id):
            raise ToolValidationError("Invalid tool ID")

    def list(self) -> list[ToolInfo]:
        return [ToolInfo.model_validate(item) for item in self.store.list()]

    def get(self, tool_id: str) -> ToolInfo:
        self._validate_tool_id(tool_id)
        item = self.store.get(tool_id)
        if item is None:
            raise ToolNotFoundError(tool_id)
        return ToolInfo.model_validate(item)

    def toggle(self, tool_id: str) -> ToolInfo:
        self._validate_tool_id(tool_id)
        updated = self.store.toggle(tool_id)
        if updated is None:
            raise ToolNotFoundError(tool_id)
        return ToolInfo.model_validate(updated)

    def delete(self, tool_id: str) -> None:
        tool = self.get(tool_id)
        if tool.is_builtin:
            raise ToolValidationError("Built-in tools cannot be deleted")
        raise ToolValidationError("Tool deletion is not supported")
