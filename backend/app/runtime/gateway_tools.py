from __future__ import annotations

import hashlib
import time
from itertools import count
from threading import Event
from typing import Any, Protocol

from langchain_core.tools import StructuredTool

from .execution_snapshot import ExecutionSnapshotPayload, SnapshotTool
from .skill_scripts import SkillScriptError, execute_script, load_script_specs
from .runner_gateway_client import RunnerGatewayBusinessError

_SAFE_MESSAGES = {
    "tool_not_authorized": "该工具当前不可用。",
    "tool_invalid_arguments": "工具参数无效。",
    "tool_duplicate_call": "工具调用标识重复。",
    "tool_execution_failed": "工具执行失败。",
    "tool_approval_required": "该工具需要人工审批后才能执行。",
    "gateway_unavailable": "Runner Gateway 不可用。",
}


class RunnerGatewayToolError(RuntimeError):
    def __init__(self, code: str, *, approval_id: str | None = None) -> None:
        self.code = code if code in _SAFE_MESSAGES else "gateway_unavailable"
        self.approval_id = approval_id
        super().__init__(_SAFE_MESSAGES[self.code])


class RunnerApprovalInterruption(RuntimeError):
    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__("该工具需要人工审批后才能执行。")


class RunnerToolClient(Protocol):
    def invoke_tool(self, **request: Any) -> dict[str, Any]: ...

    def execute_script(self, **request: Any) -> dict[str, Any]: ...


class GatewayStructuredTool(StructuredTool):
    def _to_args_and_kwargs(self, tool_input, tool_call_id):
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["_tool_call_id"] = tool_call_id
        return args, kwargs


def build_skill_resource_tools(snapshot, client, *, cancellation_event=None):
    return [
        _build_skill_resource_tool(skill, client, cancellation_event)
        for skill in snapshot.skills if skill.enabled and skill.files
    ]


def _build_skill_resource_tool(skill, client, cancellation_event):
    manifests = {item.path: item for item in skill.files}

    def read_resource(path: str, _tool_call_id: str | None = None):
        del _tool_call_id
        if path not in manifests:
            raise RunnerGatewayToolError("tool_not_authorized")
        if cancellation_event is not None and cancellation_event.is_set():
            raise RunnerGatewayToolError("gateway_unavailable")
        try:
            response = client.read_skill_file(skill.name, path)
            data = response["data"]
            manifest = manifests[path]
            if (
                response.get("skill_name") != skill.name
                or response.get("path") != path
                or not isinstance(data, bytes)
                or len(data) != manifest.size
                or hashlib.sha256(data).hexdigest() != manifest.sha256
            ):
                raise RunnerGatewayToolError("tool_execution_failed")
            return {"path": path, "content": data.decode("utf-8")}
        except (RunnerGatewayBusinessError, UnicodeDecodeError, KeyError) as error:
            raise RunnerGatewayToolError("tool_execution_failed") from error

    return GatewayStructuredTool.from_function(
        func=read_resource,
        name=f"skill.{skill.name}.resource.read",
        description=f"Read an authorized text resource from Skill {skill.name}",
        args_schema={
            "type": "object", "properties": {"path": {"type": "string", "enum": sorted(manifests)}},
            "required": ["path"], "additionalProperties": False,
        },
        infer_schema=False,
    )


def build_gateway_tools(
    snapshot: ExecutionSnapshotPayload,
    client: RunnerToolClient,
    *,
    allowed_tool_ids: set[str] | None = None,
    member_agent_id: str | None = None,
    invocation_namespace: str | None = None,
    cancellation_event: Event | None = None,
) -> list[StructuredTool]:
    return [
        _build_tool(
            tool,
            client,
            member_agent_id=member_agent_id,
            invocation_namespace=invocation_namespace,
            cancellation_event=cancellation_event,
        )
        for tool in snapshot.tools
        if tool.published
        and tool.enabled
        and tool.source_available
        and (allowed_tool_ids is None or tool.tool_id in allowed_tool_ids)
    ]


def build_skill_script_tools(
    snapshot: ExecutionSnapshotPayload,
    workspace,
    *,
    client: RunnerToolClient | None = None,
    cancellation_event: Event | None = None,
    deadline_monotonic: float | None = None,
) -> list[StructuredTool]:
    """Build declared Skill scripts as ordinary bounded model tools."""
    tools: list[StructuredTool] = []
    root = workspace / "skills" if workspace is not None else None
    if root is None:
        return tools
    for skill in snapshot.skills:
        if not skill.enabled:
            continue
        try:
            specs = load_script_specs(skill)
        except SkillScriptError:
            continue
        for spec in specs:
            skill_root = root / skill.name
            sequences = count()

            def invoke_script(_tool_call_id: str | None = None, _spec=spec, _root=skill_root, **arguments: Any):
                try:
                    lease = None
                    if client is not None and hasattr(client, "execute_script"):
                        sequence = next(sequences)
                        lease = client.execute_script(
                            script_name=_spec.tool_name,
                            arguments=arguments,
                            tool_call_id=_tool_call_id or _spec.tool_name,
                            invocation_sequence=sequence,
                            idempotency_key=f"script:{_tool_call_id or _spec.tool_name}:{sequence}",
                        )
                        if (
                            not isinstance(lease, dict)
                            or lease.get("status") != "leased"
                            or lease.get("script_name") != _spec.tool_name
                            or not isinstance(lease.get("lease_id"), str)
                            or not lease["lease_id"]
                        ):
                            raise RunnerGatewayToolError("tool_execution_failed")
                    started = time.monotonic()
                    status = "completed"
                    error_code = None
                    try:
                        return execute_script(
                            _spec, _root, arguments, cancel_event=cancellation_event,
                            deadline_monotonic=deadline_monotonic,
                        )
                    except SkillScriptError as error:
                        status = "cancelled" if "cancel" in str(error) else "failed"
                        error_code = "skill_script_cancelled" if status == "cancelled" else "skill_script_failed"
                        raise
                    except Exception:
                        status = "failed"
                        error_code = "skill_script_failed"
                        raise
                    finally:
                        if lease is not None and client is not None and hasattr(client, "complete_script"):
                            client.complete_script(
                                lease_id=lease["lease_id"], status=status, error_code=error_code,
                                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                                idempotency_key=f"script-complete:{lease['lease_id']}:{status}",
                            )
                except RunnerGatewayBusinessError as error:
                    if error.code == "tool_approval_required":
                        raise RunnerApprovalInterruption(error.details.get("approval_id", "")) from error
                    raise RunnerGatewayToolError("tool_execution_failed") from error
                except SkillScriptError as error:
                    raise RunnerGatewayToolError("tool_execution_failed") from error
                except RunnerGatewayToolError:
                    raise
                except Exception as error:
                    raise RunnerGatewayToolError("tool_execution_failed") from error

            tools.append(
                GatewayStructuredTool.from_function(
                    func=invoke_script,
                    name=spec.tool_name,
                    description=f"Execute declared Skill script {spec.name}",
                    args_schema=spec.input_schema,
                    infer_schema=False,
                )
            )
    return tools


def _build_tool(
    tool: SnapshotTool,
    client: RunnerToolClient,
    *,
    member_agent_id: str | None = None,
    invocation_namespace: str | None = None,
    cancellation_event: Event | None = None,
) -> StructuredTool:
    sequences = count()

    def invoke_gateway(_tool_call_id: str | None = None, **arguments: Any):
        if not _tool_call_id:
            raise RunnerGatewayToolError("tool_execution_failed")
        sequence = next(sequences)
        tool_call_id = _tool_call_id
        if invocation_namespace is not None:
            candidate = f"{invocation_namespace}:{_tool_call_id}"
            tool_call_id = (
                candidate
                if len(candidate) <= 128
                else f"team-tool:{hashlib.sha256(candidate.encode('utf-8')).hexdigest()}"
            )
        try:
            request = {
                "tool_id": tool.tool_id,
                "version": tool.version,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "invocation_sequence": sequence,
                "idempotency_key": f"tool:{tool_call_id}:{sequence}",
            }
            if member_agent_id is not None:
                request["member_agent_id"] = member_agent_id
            if cancellation_event is not None and cancellation_event.is_set():
                raise RunnerGatewayToolError("gateway_unavailable")
            return client.invoke_tool(
                **request,
            )
        except RunnerGatewayToolError as error:
            if (
                error.code == "tool_approval_required"
                and error.approval_id is not None
            ):
                raise RunnerApprovalInterruption(error.approval_id) from error
            raise RunnerGatewayToolError(error.code) from error

    return GatewayStructuredTool.from_function(
        func=invoke_gateway,
        name=tool.tool_id,
        description=tool.description or tool.name,
        args_schema=tool.input_schema,
        infer_schema=False,
    )
