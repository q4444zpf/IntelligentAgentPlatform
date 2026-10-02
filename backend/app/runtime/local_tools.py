"""Trusted model declarations for tools executed inside the Team runtime."""

from functools import lru_cache

from app.tools.schemas import ToolDefinition


TEAM_DISALLOWED_LOCAL_TOOLS = frozenset({"task", "execute", "delete"})
TEAM_ARTIFACT_TOOLS = frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep"})


@lru_cache(maxsize=1)
def team_local_tool_definitions() -> dict[str, ToolDefinition]:
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware import TodoListMiddleware
    from langchain_core.utils.function_calling import convert_to_openai_tool

    tools = [tool for tool in FilesystemMiddleware().tools if tool.name in TEAM_ARTIFACT_TOOLS]
    tools.extend(tool for tool in TodoListMiddleware().tools if tool.name == "write_todos")
    definitions = {}
    for tool in tools:
        function = convert_to_openai_tool(tool)["function"]
        definitions[function["name"]] = ToolDefinition(
            function["name"], function.get("description", ""), function["parameters"],
        )
    return definitions
