from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json as jsonlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from .execution_contract import RunExecutionRequest
from .http_tls import create_runtime_ssl_context
from .runner_gateway_schemas import (
    ArtifactCapabilityRegistrationRequest,
    ArtifactCapabilityResponse,
    ArtifactContentResponse,
    ArtifactCreateRequest,
    ArtifactFileResponse,
    CheckpointResponse,
    EventAppendResponse,
    ModelInvocationResponse,
    SnapshotResponse,
    SkillFileResponse,
    ToolInvocationResponse,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
_COMPLETION_BUDGET_SECONDS = 1.0

_SAFE_MESSAGES = {
    "sandbox_timeout": "Sandbox execution timed out.",
    "runner_gateway_unavailable": "Runner Gateway 不可用。",
    "runner_gateway_response_invalid": "Runner Gateway 返回无效响应。",
    "runner_gateway_forbidden": "Runner Gateway 操作未授权。",
    "runner_gateway_not_found": "Runner Gateway 资源不存在。",
    "runner_gateway_conflict": "Runner Gateway 请求冲突。",
    "runner_gateway_failed": "Runner Gateway 请求失败。",
}


class RunnerGatewayClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_SAFE_MESSAGES.get(code, _SAFE_MESSAGES["runner_gateway_failed"]))


class RunnerGatewayUnavailable(RunnerGatewayClientError):
    def __init__(self) -> None:
        super().__init__("runner_gateway_unavailable")


class RunnerGatewayDeadlineExceeded(RunnerGatewayUnavailable):
    def __init__(self) -> None:
        RunnerGatewayClientError.__init__(self, "sandbox_timeout")


class RunnerGatewayResponseInvalid(RunnerGatewayClientError):
    def __init__(self) -> None:
        super().__init__("runner_gateway_response_invalid")


class RunnerGatewayBusinessError(RunnerGatewayClientError):
    def __init__(self, code: str, *, details: dict[str, str] | None = None) -> None:
        self.details = details or {}
        super().__init__(code)


@dataclass
class RunnerGatewayClient:
    base_url: str
    run_id: str
    token: str = field(repr=False)
    snapshot_digest: str = ""
    request_deadline_at: datetime | None = None
    execution_deadline_at: datetime | None = None
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    max_response_bytes: int = 4 * 1024 * 1024

    @classmethod
    def from_execution_request(
        cls,
        request: RunExecutionRequest,
        *,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> RunnerGatewayClient:
        return cls(
            base_url=request.gateway_url,
            run_id=request.run_id,
            token=request.run_token,
            snapshot_digest=request.snapshot_digest,
            request_deadline_at=request.deadline_at,
            execution_deadline_at=request.execution_deadline_at,
            transport=transport,
            max_response_bytes=max_response_bytes,
        )

    def get_snapshot(self) -> SnapshotResponse:
        return self._request("GET", "snapshot", SnapshotResponse)

    def read_skill_file(self, skill_name: str, path: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"skills/{quote(skill_name, safe='')}/files/{quote(path, safe='/')}",
            SkillFileResponse,
        )
        try:
            data = base64.b64decode(response.data_base64, validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise RunnerGatewayResponseInvalid() from None
        if len(data) != response.size or hashlib.sha256(data).hexdigest() != response.sha256:
            raise RunnerGatewayResponseInvalid()
        value = response.model_dump(mode="json", exclude={"data_base64"})
        value["data"] = data
        return value

    def get_latest_checkpoint(self) -> dict[str, Any]:
        return self._request(
            "GET", "checkpoints/latest", CheckpointResponse
        ).model_dump(mode="json")

    def save_checkpoint(
        self,
        checkpoint_key: str,
        state: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"checkpoints/{checkpoint_key}",
            CheckpointResponse,
            json={"state": state},
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def append_event(
        self,
        *,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "events",
            EventAppendResponse,
            json={
                "sequence": sequence,
                "event_type": event_type,
                "payload": payload,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def invoke_model(
        self, request: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "model-invocations",
            ModelInvocationResponse,
            json=request,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def invoke_tool(
        self,
        *,
        tool_id: str,
        version: str,
        tool_call_id: str,
        member_agent_id: str | None = None,
        arguments: dict[str, Any],
        invocation_sequence: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "tool_id": tool_id,
            "version": version,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "invocation_sequence": invocation_sequence,
        }
        if member_agent_id is not None:
            request["member_agent_id"] = member_agent_id
        return self._request(
            "POST",
            "tool-invocations",
            ToolInvocationResponse,
            json=request,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def create_artifact(
        self,
        *,
        path: str,
        data: bytes,
        content_type: str,
        sha256: str,
        provenance: dict[str, str] | None = None,
        capability: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = ArtifactCreateRequest(
            path=path,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
            data_base64=base64.b64encode(data).decode("ascii"),
            provenance=provenance,
            capability=capability,
        )
        return self._request(
            "POST",
            "artifacts",
            ArtifactFileResponse,
            json=request.model_dump(mode="json", exclude_none=True),
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def register_artifact_capability(
        self,
        *,
        team_version_id: str,
        member_agent_id: str,
        task_id: str,
        invocation_id: str,
    ) -> str:
        request = ArtifactCapabilityRegistrationRequest(
            team_version_id=team_version_id,
            member_agent_id=member_agent_id,
            task_id=task_id,
            invocation_id=invocation_id,
        )
        return self._request(
            "POST",
            "artifact-capabilities",
            ArtifactCapabilityResponse,
            json=request.model_dump(mode="json"),
        ).capability

    def list_artifacts(self) -> list[dict[str, Any]]:
        values = self._request_adapter(
            "GET",
            "artifacts",
            TypeAdapter(list[ArtifactFileResponse]),
        )
        return [value.model_dump(mode="json") for value in values]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"artifacts/{artifact_id}",
            ArtifactContentResponse,
        )
        try:
            data = base64.b64decode(response.data_base64, validate=True)
        except ValueError as error:
            raise RunnerGatewayResponseInvalid() from error
        value = response.model_dump(mode="json", exclude={"data_base64"})
        value["data"] = data
        return value

    def complete(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        deadline_at = now + timedelta(seconds=_COMPLETION_BUDGET_SECONDS)
        if self.request_deadline_at is not None:
            deadline_at = min(deadline_at, self.request_deadline_at)
        return self._request_adapter(
            "POST",
            "completion",
            TypeAdapter(dict[str, Any]),
            json=request,
            idempotency_key=idempotency_key,
            deadline_at=deadline_at,
        )

    def _request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseModel],
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ResponseModel:
        return self._request_adapter(
            method,
            path,
            TypeAdapter(response_model),
            json=json,
            idempotency_key=idempotency_key,
        )

    def _request_adapter(
        self,
        method: str,
        path: str,
        adapter: TypeAdapter,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        deadline_at: datetime | None = None,
    ):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Run-Id": self.run_id,
            "X-Snapshot-Digest": self.snapshot_digest,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        selected_deadline = (
            deadline_at
            if deadline_at is not None
            else self.execution_deadline_at
        )
        uses_execution_deadline = deadline_at is None
        try:
            remaining = self._remaining_seconds(selected_deadline)
        except RunnerGatewayUnavailable as error:
            if uses_execution_deadline:
                raise RunnerGatewayDeadlineExceeded() from error
            raise
        monotonic_deadline = time.monotonic() + remaining
        try:
            if self.transport is None:
                status_code, content = asyncio.run(
                    self._async_request_content(
                        method,
                        path,
                        headers=headers,
                        json=json,
                        monotonic_deadline=monotonic_deadline,
                    )
                )
            else:
                status_code, content = self._sync_request_content(
                    method,
                    path,
                    headers=headers,
                    json=json,
                    selected_deadline=selected_deadline,
                    monotonic_deadline=monotonic_deadline,
                )
            if time.monotonic() >= monotonic_deadline:
                raise TimeoutError("Runner Gateway deadline expired")
        except TimeoutError as error:
            if uses_execution_deadline:
                raise RunnerGatewayDeadlineExceeded() from error
            raise RunnerGatewayUnavailable() from error
        except httpx.HTTPError as error:
            if (
                uses_execution_deadline
                and time.monotonic() >= monotonic_deadline
            ):
                raise RunnerGatewayDeadlineExceeded() from error
            raise RunnerGatewayUnavailable() from error
        try:
            payload = jsonlib.loads(content)
        except (TypeError, ValueError) as error:
            self._raise_if_deadline_expired(
                monotonic_deadline,
                uses_execution_deadline=uses_execution_deadline,
            )
            raise RunnerGatewayResponseInvalid() from error
        self._raise_if_deadline_expired(
            monotonic_deadline,
            uses_execution_deadline=uses_execution_deadline,
        )
        if status_code >= 400:
            self._raise_business_error(status_code, payload)
        try:
            value = adapter.validate_python(payload)
        except (TypeError, ValueError, ValidationError) as error:
            self._raise_if_deadline_expired(
                monotonic_deadline,
                uses_execution_deadline=uses_execution_deadline,
            )
            raise RunnerGatewayResponseInvalid() from error
        self._raise_if_deadline_expired(
            monotonic_deadline,
            uses_execution_deadline=uses_execution_deadline,
        )
        return value

    @staticmethod
    def _raise_if_deadline_expired(
        monotonic_deadline: float,
        *,
        uses_execution_deadline: bool,
    ) -> None:
        if time.monotonic() < monotonic_deadline:
            return
        if uses_execution_deadline:
            raise RunnerGatewayDeadlineExceeded()
        raise RunnerGatewayUnavailable()

    def _sync_request_content(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        selected_deadline: datetime | None,
        monotonic_deadline: float,
    ) -> tuple[int, bytearray]:
        with httpx.Client(
            timeout=self._timeout(selected_deadline),
            transport=self.transport,
            trust_env=False,
        ) as client, client.stream(
            method,
            f"{self.base_url.rstrip('/')}/runs/{self.run_id}/{path}",
            headers=headers,
            json=json,
        ) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() >= monotonic_deadline:
                    raise TimeoutError("Runner Gateway deadline expired")
                content.extend(chunk)
                if len(content) > self.max_response_bytes:
                    raise RunnerGatewayResponseInvalid()
            return response.status_code, content

    async def _async_request_content(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        monotonic_deadline: float,
    ) -> tuple[int, bytearray]:
        remaining = monotonic_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Runner Gateway deadline expired")
        async with asyncio.timeout(remaining):
            context = await create_runtime_ssl_context()
            remaining = monotonic_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Runner Gateway deadline expired")
            async with httpx.AsyncClient(
                timeout=self._timeout_from_remaining(remaining),
                trust_env=False,
                verify=context,
            ) as client, client.stream(
                method,
                f"{self.base_url.rstrip('/')}/runs/{self.run_id}/{path}",
                headers=headers,
                json=json,
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if time.monotonic() >= monotonic_deadline:
                        raise TimeoutError("Runner Gateway deadline expired")
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise RunnerGatewayResponseInvalid()
                status_code = response.status_code
        if time.monotonic() >= monotonic_deadline:
            raise TimeoutError("Runner Gateway deadline expired")
        return status_code, content

    @staticmethod
    def _remaining_seconds(deadline_at: datetime | None) -> float:
        remaining = _DEFAULT_REQUEST_TIMEOUT_SECONDS
        if deadline_at is not None:
            remaining = min(
                remaining,
                (deadline_at - datetime.now(timezone.utc)).total_seconds(),
            )
        if remaining <= 0:
            raise RunnerGatewayUnavailable()
        return remaining

    @classmethod
    def _timeout(cls, deadline_at: datetime | None) -> httpx.Timeout:
        return cls._timeout_from_remaining(cls._remaining_seconds(deadline_at))

    @staticmethod
    def _timeout_from_remaining(remaining: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=min(3.0, remaining),
            read=remaining,
            write=remaining,
            pool=min(3.0, remaining),
        )

    @staticmethod
    def _raise_business_error(status_code: int, payload: Any) -> None:
        code = {
            403: "runner_gateway_forbidden",
            404: "runner_gateway_not_found",
            409: "runner_gateway_conflict",
        }.get(status_code, "runner_gateway_failed")
        details: dict[str, str] = {}
        if isinstance(payload, dict):
            if isinstance(payload.get("code"), str):
                code = payload["code"]
            if isinstance(payload.get("approval_id"), str):
                details["approval_id"] = payload["approval_id"]
        raise RunnerGatewayBusinessError(code, details=details)
