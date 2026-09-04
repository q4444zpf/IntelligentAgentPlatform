from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublishedToolSnapshot:
    name: str
    description: str
    input_schema: dict[str, Any]
    published: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class PublishedSkillSnapshot:
    name: str
    content: str = ""


@dataclass(frozen=True)
class PublishedAgentSnapshot:
    agent_id: str
    name: str
    system_prompt: str
    context_prompt: str
    tools: tuple[PublishedToolSnapshot, ...]
    skills: tuple[PublishedSkillSnapshot, ...] = ()
    knowledge_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


def create_in_memory_checkpointer():
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as error:
        raise RuntimeError("LangGraph runtime is not installed") from error
    return InMemorySaver()


class DeepAgentFactory:
    def __init__(self, creator: Callable[..., Any] | None = None):
        self.creator = creator or self._default_creator

    @staticmethod
    def _default_creator(**kwargs):
        try:
            from deepagents import create_deep_agent
        except ImportError as error:
            raise RuntimeError("Deep Agents runtime is not installed") from error
        return create_deep_agent(**kwargs)

    def build(
        self,
        snapshot: PublishedAgentSnapshot,
        *,
        model: Any,
        tools: list[Any] | None = None,
        backend: Any | None = None,
        checkpointer: Any | None = None,
    ) -> Any:
        if not snapshot.agent_id:
            raise ValueError("agent_id is required")
        prompt = snapshot.system_prompt.strip()
        context = snapshot.context_prompt.strip()
        if context:
            prompt = f"{prompt}\n\n{context}" if prompt else context
        skill_content = "\n\n".join(
            skill.content.strip() for skill in snapshot.skills if skill.content.strip()
        )
        if skill_content:
            prompt = f"{prompt}\n\n{skill_content}" if prompt else skill_content
        resolved_tools = tools if tools is not None else [
            AgentToolDefinition(tool.name, tool.description, tool.input_schema)
            for tool in snapshot.tools
            if tool.published and tool.enabled
        ]
        arguments = {
            "model": model,
            "system_prompt": prompt,
            "tools": resolved_tools,
            "name": snapshot.agent_id,
        }
        if backend is not None:
            arguments["backend"] = backend
        if checkpointer is not None:
            arguments["checkpointer"] = checkpointer
        return self.creator(
            **arguments,
        )
