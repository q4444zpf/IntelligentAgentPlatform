from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from .http_tls import create_runtime_ssl_context


_DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0


class LauncherClientError(RuntimeError):
    pass


class LauncherDeadlineExceededError(LauncherClientError):
    pass


class LauncherTransport(Protocol):
    def create(
        self,
        run_id: str,
        workspace_path: str,
        execution: dict[str, str],
        *,
        deadline_at: datetime | None = None,
    ) -> dict[str, Any]: ...
    def inspect(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]: ...
    def terminate(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]: ...
    def cleanup(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]: ...


@dataclass
class LauncherHttpTransport:
    base_url: str
    token: str
    request: Callable[..., dict[str, Any]] | None = None
    monotonic: Callable[[], float] = time.monotonic

    def _headers(self, run_id: str, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "X-Run-Id": run_id}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def create(
        self,
        run_id: str,
        workspace_path: str,
        execution: dict[str, str],
        *,
        deadline_at: datetime | None = None,
    ) -> dict[str, Any]:
        body = json.dumps({"workspace_path": workspace_path, **execution}).encode("utf-8")
        return self._request(
            "POST", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id, json_body=True), body=body,
            deadline_at=deadline_at,
        )

    def inspect(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id),
            deadline_at=deadline_at,
        )

    def cleanup(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]:
        return self._request(
            "DELETE", f"{self.base_url.rstrip('/')}/runs/{run_id}/container",
            headers=self._headers(run_id),
            deadline_at=deadline_at,
        )

    def terminate(
        self, run_id: str, *, deadline_at: datetime | None = None
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"{self.base_url.rstrip('/')}/runs/{run_id}/container/terminate",
            headers=self._headers(run_id),
            deadline_at=deadline_at,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        deadline_at: datetime | None = None,
    ) -> dict[str, Any]:
        if deadline_at is None:
            deadline_at = datetime.now(UTC) + timedelta(
                seconds=_DEFAULT_REQUEST_TIMEOUT_SECONDS
            )
        remaining = (deadline_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("Sandbox Launcher deadline expired")
        monotonic_deadline = self.monotonic() + remaining
        request_headers = dict(headers)
        request_headers["X-Request-Deadline-At"] = deadline_at.isoformat()
        if self.request is not None:
            result = self.request(
                method,
                url,
                headers=request_headers,
                body=body,
            )
        else:
            result = asyncio.run(
                self._async_request(
                    method,
                    url,
                    headers=request_headers,
                    body=body,
                    monotonic_deadline=monotonic_deadline,
                    monotonic=self.monotonic,
                )
            )
        if self.monotonic() >= monotonic_deadline:
            raise TimeoutError("Sandbox Launcher deadline expired")
        return result

    @staticmethod
    async def _async_request(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        monotonic_deadline: float,
        monotonic: Callable[[], float],
    ) -> dict[str, Any]:
        remaining = monotonic_deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Sandbox Launcher deadline expired")
        async with asyncio.timeout(remaining):
            context = await create_runtime_ssl_context()
            remaining = monotonic_deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Sandbox Launcher deadline expired")
            phase_timeout = httpx.Timeout(
                connect=min(3.0, remaining),
                pool=min(3.0, remaining),
                read=remaining,
                write=remaining,
            )
            async with httpx.AsyncClient(
                timeout=phase_timeout,
                trust_env=False,
                verify=context,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    content=body,
                )
                response.raise_for_status()
                payload = response.json()
        if monotonic() >= monotonic_deadline:
            raise TimeoutError("Sandbox Launcher deadline expired")
        return payload


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
        execution_deadline_at: str,
        snapshot_id: str,
        snapshot_digest: str,
        gateway_url: str,
        run_token: str,
        request_deadline_at: str | None = None,
    ) -> dict[str, Any]:
        execution_deadline = self._parse_deadline(execution_deadline_at)
        transport_deadline = execution_deadline
        if request_deadline_at is not None:
            transport_deadline = min(
                transport_deadline,
                self._parse_deadline(request_deadline_at),
            )
        if datetime.now(UTC) >= transport_deadline:
            raise LauncherDeadlineExceededError(
                "sandbox execution deadline expired"
            )
        try:
            self.transport.create(run_id, f"/workspace/{run_id}", {
                "agent_version": agent_version,
                "checkpoint_key": checkpoint_key,
                "deadline_at": deadline_at,
                "execution_deadline_at": execution_deadline_at,
                "snapshot_id": snapshot_id,
                "snapshot_digest": snapshot_digest,
                "gateway_url": gateway_url,
                "run_token": run_token,
            }, deadline_at=transport_deadline)
            try:
                inspected = self.transport.inspect(
                    run_id,
                    deadline_at=transport_deadline,
                )
            except LauncherDeadlineExceededError:
                raise
            except Exception as exc:
                if self._is_deadline_error(exc, deadline_at=transport_deadline):
                    raise LauncherDeadlineExceededError(
                        "sandbox execution deadline expired"
                    ) from exc
                self.transport.cleanup(
                    run_id,
                    deadline_at=transport_deadline,
                )
                raise LauncherClientError("sandbox launcher is unavailable") from exc
            if inspected.get("status") != "running":
                self.transport.cleanup(
                    run_id,
                    deadline_at=transport_deadline,
                )
                raise LauncherClientError("sandbox container is not running")
            return inspected
        except LauncherClientError:
            raise
        except Exception as exc:
            if self._is_deadline_error(exc, deadline_at=transport_deadline):
                raise LauncherDeadlineExceededError(
                    "sandbox execution deadline expired"
                ) from exc
            raise LauncherClientError("sandbox launcher is unavailable") from exc

    def inspect(
        self, run_id: str, *, request_deadline_at: str | None = None
    ) -> dict[str, Any]:
        return self._lifecycle_call(
            self.transport.inspect,
            run_id,
            deadline_at=self._optional_deadline(request_deadline_at),
        )

    def terminate(
        self, run_id: str, *, request_deadline_at: str | None = None
    ) -> dict[str, Any]:
        return self._lifecycle_call(
            self.transport.terminate,
            run_id,
            deadline_at=self._optional_deadline(request_deadline_at),
        )

    def cleanup(
        self, run_id: str, *, request_deadline_at: str | None = None
    ) -> dict[str, Any]:
        return self._lifecycle_call(
            self.transport.cleanup,
            run_id,
            deadline_at=self._optional_deadline(request_deadline_at),
        )

    @staticmethod
    def _lifecycle_call(
        operation,
        run_id: str,
        *,
        deadline_at: datetime | None,
    ) -> dict[str, Any]:
        try:
            result = operation(run_id, deadline_at=deadline_at)
        except Exception as exc:
            if LauncherClient._is_deadline_error(exc, deadline_at=deadline_at):
                raise LauncherDeadlineExceededError(
                    "sandbox execution deadline expired"
                ) from exc
            raise LauncherClientError("sandbox launcher is unavailable") from exc
        if not isinstance(result, dict):
            raise LauncherClientError("sandbox launcher returned an invalid response")
        return result

    @staticmethod
    def _is_deadline_error(
        error: Exception,
        *,
        deadline_at: datetime | None,
    ) -> bool:
        return (
            isinstance(error, LauncherDeadlineExceededError)
            or (
                isinstance(error, (TimeoutError, httpx.TimeoutException))
                and deadline_at is not None
                and datetime.now(UTC) >= deadline_at
            )
            or (
                isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code == 504
            )
        )

    @staticmethod
    def _parse_deadline(value: str) -> datetime:
        try:
            deadline_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise LauncherClientError("sandbox execution deadline is invalid") from error
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise LauncherClientError("sandbox execution deadline is invalid")
        return deadline_at.astimezone(UTC)

    @classmethod
    def _optional_deadline(cls, value: str | None) -> datetime | None:
        return cls._parse_deadline(value) if value is not None else None


def launcher_client_from_env() -> LauncherClient | None:
    url = os.getenv("IAP_SANDBOX_LAUNCHER_URL", "").strip()
    token = os.getenv("IAP_RUNNER_LAUNCHER_TOKEN", "")
    if not url or not token:
        return None
    return LauncherClient(LauncherHttpTransport(url, token))
