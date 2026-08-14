from __future__ import annotations

import base64
import json as jsonlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from .execution_contract import RunExecutionRequest
from .runner_gateway_schemas import (
    ArtifactContentResponse,
    ArtifactCreateRequest,
    ArtifactFileResponse,
    CheckpointResponse,
    EventAppendResponse,
    ModelInvocationResponse,
    SnapshotResponse,
    ToolInvocationResponse,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

_SAFE_MESSAGES = {
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
    deadline_at: datetime | None = None
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
            deadline_at=request.deadline_at,
            transport=transport,
            max_response_bytes=max_response_bytes,
        )

    def get_snapshot(self) -> SnapshotResponse:
        return self._request("GET", "snapshot", SnapshotResponse)

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
        arguments: dict[str, Any],
        invocation_sequence: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "tool-invocations",
            ToolInvocationResponse,
            json={
                "tool_id": tool_id,
                "version": version,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "invocation_sequence": invocation_sequence,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    def create_artifact(
        self,
        *,
        path: str,
        data: bytes,
        content_type: str,
        sha256: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = ArtifactCreateRequest(
            path=path,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
            data_base64=base64.b64encode(data).decode("ascii"),
        )
        return self._request(
            "POST",
            "artifacts",
            ArtifactFileResponse,
            json=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

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
        return self._request_adapter(
            "POST",
            "completion",
            TypeAdapter(dict[str, Any]),
            json=request,
            idempotency_key=idempotency_key,
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
    ):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Run-Id": self.run_id,
            "X-Snapshot-Digest": self.snapshot_digest,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(
                timeout=self._timeout(),
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
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise RunnerGatewayResponseInvalid()
                status_code = response.status_code
        except httpx.HTTPError as error:
            raise RunnerGatewayUnavailable() from error
        try:
            payload = jsonlib.loads(content)
        except (TypeError, ValueError) as error:
            raise RunnerGatewayResponseInvalid() from error
        if status_code >= 400:
            self._raise_business_error(status_code, payload)
        try:
            return adapter.validate_python(payload)
        except (TypeError, ValueError, ValidationError) as error:
            raise RunnerGatewayResponseInvalid() from error

    def _timeout(self) -> httpx.Timeout:
        remaining = 90.0
        if self.deadline_at is not None:
            remaining = max(
                0.1,
                min(
                    remaining,
                    (self.deadline_at - datetime.now(timezone.utc)).total_seconds(),
                ),
            )
        return httpx.Timeout(connect=3.0, read=remaining, write=remaining, pool=3.0)

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
