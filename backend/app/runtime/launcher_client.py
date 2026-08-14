from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class LauncherClientError(RuntimeError):
    pass


class LauncherTransport(Protocol):
    def create(self, run_id: str, workspace_path: str, execution: dict[str, str]) -> dict[str, Any]: ...
    def inspect(self, run_id: str) -> dict[str, Any]: ...
    def terminate(self, run_id: str) -> dict[str, Any]: ...
    def cleanup(self, run_id: str) -> dict[str, Any]: ...


def _urlopen_request(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class LauncherHttpTransport:
    base_url: str
    token: str
    request: Callable[..., dict[str, Any]] = _urlopen_request

    def _headers(self, run_id: str, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "X-Run-Id": run_id}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def create(self, run_id: str, workspace_path: str, execution: dict[str, str]) -> dict[str, Any]:
        body = json.dumps({"workspace_path": workspace_path, **execution}).encode("utf-8")
        return self.request(
            "POST", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id, json_body=True), body=body,
        )

    def inspect(self, run_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id),
        )

    def cleanup(self, run_id: str) -> dict[str, Any]:
        return self.request(
            "DELETE", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id),
        )

    def terminate(self, run_id: str) -> dict[str, Any]:
        return self.request(
            "POST", f"{self.base_url.rstrip('/')}/runs/{run_id}/container/terminate",
            headers=self._headers(run_id),
        )


@dataclass
class LauncherClient:
    transport: LauncherTransport

    def prepare(
        self,
        run_id: str,
        *,
        agent_version: str,
        checkpoint_key: str,
        deadline_at: str,
        snapshot_id: str,
        snapshot_digest: str,
        gateway_url: str,
        run_token: str,
    ) -> dict[str, Any]:
        try:
            self.transport.create(run_id, f"/workspace/{run_id}", {
                "agent_version": agent_version,
                "checkpoint_key": checkpoint_key,
                "deadline_at": deadline_at,
                "snapshot_id": snapshot_id,
                "snapshot_digest": snapshot_digest,
                "gateway_url": gateway_url,
                "run_token": run_token,
            })
            try:
                inspected = self.transport.inspect(run_id)
            except Exception as exc:
                self.transport.cleanup(run_id)
                raise LauncherClientError("sandbox launcher is unavailable") from exc
            if inspected.get("status") != "running":
                self.transport.cleanup(run_id)
                raise LauncherClientError("sandbox container is not running")
            return inspected
        except LauncherClientError:
            raise
        except Exception as exc:
            raise LauncherClientError("sandbox launcher is unavailable") from exc

    def inspect(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.inspect, run_id)

    def terminate(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.terminate, run_id)

    def cleanup(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle_call(self.transport.cleanup, run_id)

    @staticmethod
    def _lifecycle_call(operation, run_id: str) -> dict[str, Any]:
        try:
            result = operation(run_id)
        except Exception as exc:
            raise LauncherClientError("sandbox launcher is unavailable") from exc
        if not isinstance(result, dict):
            raise LauncherClientError("sandbox launcher returned an invalid response")
        return result


def launcher_client_from_env() -> LauncherClient | None:
    url = os.getenv("IAP_SANDBOX_LAUNCHER_URL", "").strip()
    token = os.getenv("IAP_RUNNER_LAUNCHER_TOKEN", "")
    if not url or not token:
        return None
    return LauncherClient(LauncherHttpTransport(url, token))
