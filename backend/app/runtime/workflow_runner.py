from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class RunnerUnavailableError(RuntimeError):
    pass


class RunnerTransport(Protocol):
    def health_check(self) -> dict[str, Any]: ...
    def submit(self, payload: dict[str, str]) -> dict[str, Any]: ...
    def status(self, run_id: str) -> dict[str, Any]: ...
    def terminate(self, run_id: str) -> dict[str, Any]: ...
    def cleanup(self, run_id: str) -> dict[str, Any]: ...


def _urlopen_request(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class WorkflowRunnerHttpTransport:
    base_url: str
    request: Any = _urlopen_request

    def health_check(self) -> dict[str, Any]:
        return self.request("GET", f"{self.base_url.rstrip('/')}/health", headers={})

    def submit(self, payload: dict[str, str]) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{self.base_url.rstrip('/')}/runs",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )

    def status(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", f"{self.base_url.rstrip('/')}/runs/{run_id}", headers={})

    def terminate(self, run_id: str) -> dict[str, Any]:
        return self.request("POST", f"{self.base_url.rstrip('/')}/runs/{run_id}/terminate", headers={})

    def cleanup(self, run_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"{self.base_url.rstrip('/')}/runs/{run_id}", headers={})


@dataclass
class WorkflowRunnerClient:
    transport: RunnerTransport

    def is_healthy(self) -> bool:
        try:
            health = self.transport.health_check()
        except Exception:  # noqa: BLE001
            return False
        return health.get("status") == "healthy" and health.get("sandbox") is True

    def submit(
        self,
        run_id: str,
        agent_version: str,
        checkpoint_key: str,
        *,
        snapshot_id: str,
        snapshot_digest: str,
        gateway_url: str,
        run_token: str,
        deadline_at: str,
    ) -> dict[str, Any]:
        if not self.is_healthy():
            raise RunnerUnavailableError("Workflow Runner is unavailable")
        return self.transport.submit({
            "run_id": run_id,
            "agent_version": agent_version,
            "checkpoint_key": checkpoint_key,
            "snapshot_id": snapshot_id,
            "snapshot_digest": snapshot_digest,
            "gateway_url": gateway_url,
            "run_token": run_token,
            "deadline_at": deadline_at,
        })

    def status(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.status, run_id)

    def terminate(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.terminate, run_id)

    def cleanup(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.cleanup, run_id)

    @staticmethod
    def _lifecycle_call(operation, run_id: str) -> dict[str, Any]:
        try:
            result = operation(run_id)
        except Exception as exc:
            raise RunnerUnavailableError("Workflow Runner is unavailable") from exc
        if not isinstance(result, dict):
            raise RunnerUnavailableError("Workflow Runner returned an invalid response")
        return result


def workflow_runner_client_from_env() -> WorkflowRunnerClient | None:
    enabled = os.getenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false").lower() in {"1", "true", "yes"}
    url = os.getenv("IAP_WORKFLOW_RUNNER_URL", "").strip()
    if not enabled or not url:
        return None
    return WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))
