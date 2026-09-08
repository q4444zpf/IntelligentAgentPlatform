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


class RunnerUnavailableError(RuntimeError):
    pass


class RunnerDeadlineExceededError(RunnerUnavailableError):
    pass


class RunnerTransport(Protocol):
    def health_check(
        self, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]: ...
    def submit(
        self,
        payload: dict[str, str],
        *,
        monotonic_deadline: float | None = None,
    ) -> dict[str, Any]: ...
    def status(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]: ...
    def terminate(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]: ...
    def cleanup(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]: ...


@dataclass
class WorkflowRunnerHttpTransport:
    base_url: str
    request: Any | None = None
    monotonic: Callable[[], float] = time.monotonic

    def health_check(
        self, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self.base_url.rstrip('/')}/health",
            headers={},
            monotonic_deadline=monotonic_deadline,
        )

    def submit_when_healthy(
        self,
        payload: dict[str, str],
        *,
        monotonic_deadline: float | None = None,
    ) -> dict[str, Any]:
        deadline = (
            monotonic_deadline
            if monotonic_deadline is not None
            else self.monotonic() + _DEFAULT_REQUEST_TIMEOUT_SECONDS
        )
        if self.monotonic() >= deadline:
            raise TimeoutError("Workflow Runner deadline expired")
        result = asyncio.run(
            self._async_submit_when_healthy(
                payload,
                deadline=deadline,
                has_explicit_deadline=monotonic_deadline is not None,
            )
        )
        if monotonic_deadline is not None and self.monotonic() >= deadline:
            raise TimeoutError("Workflow Runner deadline expired")
        return result

    async def _async_submit_when_healthy(
        self,
        payload: dict[str, str],
        *,
        deadline: float,
        has_explicit_deadline: bool,
    ) -> dict[str, Any]:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise TimeoutError("Workflow Runner deadline expired")
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                context = await create_runtime_ssl_context()
                if self.monotonic() >= deadline:
                    raise TimeoutError("Workflow Runner deadline expired")
                async with httpx.AsyncClient(trust_env=False, verify=context) as client:
                    health = await self._request_with_client(
                        client,
                        "GET",
                        f"{self.base_url.rstrip('/')}/health",
                        headers={},
                        body=None,
                        monotonic_deadline=deadline,
                        monotonic=self.monotonic,
                        send_deadline_header=has_explicit_deadline,
                    )
                    if not isinstance(health, dict) or not (
                        health.get("status") == "healthy" and health.get("sandbox") is True
                    ):
                        raise RunnerUnavailableError("Workflow Runner is unavailable")
                    if not has_explicit_deadline:
                        # The legacy default gives each request its own ten seconds.
                        deadline = self.monotonic() + _DEFAULT_REQUEST_TIMEOUT_SECONDS
                        timeout.reschedule(
                            asyncio.get_running_loop().time() + _DEFAULT_REQUEST_TIMEOUT_SECONDS
                        )
                    result = await self._request_with_client(
                        client,
                        "POST",
                        f"{self.base_url.rstrip('/')}/runs",
                        headers={"Content-Type": "application/json"},
                        body=json.dumps(payload).encode("utf-8"),
                        monotonic_deadline=deadline,
                        monotonic=self.monotonic,
                        send_deadline_header=has_explicit_deadline,
                    )
        except TimeoutError as exc:
            if has_explicit_deadline and timeout.expired():
                raise RunnerDeadlineExceededError("Workflow Runner deadline expired") from exc
            raise
        if self.monotonic() >= deadline:
            raise TimeoutError("Workflow Runner deadline expired")
        return result

    def submit(
        self,
        payload: dict[str, str],
        *,
        monotonic_deadline: float | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.base_url.rstrip('/')}/runs",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            monotonic_deadline=monotonic_deadline,
        )

    def status(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self.base_url.rstrip('/')}/runs/{run_id}",
            headers={},
            monotonic_deadline=monotonic_deadline,
        )

    def terminate(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.base_url.rstrip('/')}/runs/{run_id}/terminate",
            headers={},
            monotonic_deadline=monotonic_deadline,
        )

    def cleanup(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"{self.base_url.rstrip('/')}/runs/{run_id}",
            headers={},
            monotonic_deadline=monotonic_deadline,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        monotonic_deadline: float | None = None,
    ) -> dict[str, Any]:
        deadline = (
            monotonic_deadline
            if monotonic_deadline is not None
            else self.monotonic() + _DEFAULT_REQUEST_TIMEOUT_SECONDS
        )
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise TimeoutError("Workflow Runner deadline expired")
        request_headers = dict(headers)
        if monotonic_deadline is not None:
            request_headers["X-Request-Deadline-At"] = (
                datetime.now(UTC) + timedelta(seconds=remaining)
            ).isoformat()
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
                    monotonic_deadline=deadline,
                    monotonic=self.monotonic,
                    send_deadline_header=monotonic_deadline is not None,
                )
            )
        if self.monotonic() >= deadline:
            raise TimeoutError("Workflow Runner deadline expired")
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
        send_deadline_header: bool,
    ) -> dict[str, Any]:
        remaining = monotonic_deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Workflow Runner deadline expired")
        async with asyncio.timeout(remaining):
            context = await create_runtime_ssl_context()
            if monotonic() >= monotonic_deadline:
                raise TimeoutError("Workflow Runner deadline expired")
            async with httpx.AsyncClient(
                trust_env=False,
                verify=context,
            ) as client:
                payload = await WorkflowRunnerHttpTransport._request_with_client(
                    client,
                    method,
                    url,
                    headers=headers,
                    body=body,
                    monotonic_deadline=monotonic_deadline,
                    monotonic=monotonic,
                    send_deadline_header=send_deadline_header,
                )
        if monotonic() >= monotonic_deadline:
            raise TimeoutError("Workflow Runner deadline expired")
        return payload

    @staticmethod
    async def _request_with_client(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        monotonic_deadline: float,
        monotonic: Callable[[], float],
        send_deadline_header: bool,
    ) -> dict[str, Any]:
        remaining = monotonic_deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Workflow Runner deadline expired")
        request_headers = dict(headers)
        if send_deadline_header:
            request_headers["X-Request-Deadline-At"] = (
                datetime.now(UTC) + timedelta(seconds=remaining)
            ).isoformat()
        response = await client.request(
            method,
            url,
            headers=request_headers,
            content=body,
            timeout=httpx.Timeout(
                connect=min(3.0, remaining),
                pool=min(3.0, remaining),
                read=remaining,
                write=remaining,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        if monotonic() >= monotonic_deadline:
            raise TimeoutError("Workflow Runner deadline expired")
        return payload


@dataclass
class WorkflowRunnerClient:
    transport: RunnerTransport

    def is_healthy(self, *, monotonic_deadline: float | None = None) -> bool:
        try:
            health = self.transport.health_check(
                monotonic_deadline=monotonic_deadline
            )
        except RunnerDeadlineExceededError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            if (
                monotonic_deadline is not None
                and time.monotonic() >= monotonic_deadline
            ):
                raise RunnerDeadlineExceededError(
                    "Workflow Runner deadline expired"
                ) from exc
            return False
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 504:
                raise RunnerDeadlineExceededError(
                    "Workflow Runner deadline expired"
                ) from exc
            return False
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
        execution_deadline_at: str,
        monotonic_deadline: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "run_id": run_id,
            "agent_version": agent_version,
            "checkpoint_key": checkpoint_key,
            "snapshot_id": snapshot_id,
            "snapshot_digest": snapshot_digest,
            "gateway_url": gateway_url,
            "run_token": run_token,
            "deadline_at": deadline_at,
            "execution_deadline_at": execution_deadline_at,
        }
        # Custom transports and overridden health/submit behavior retain their path.
        if (
            type(self) is WorkflowRunnerClient
            and "is_healthy" not in vars(self)
            and type(self.transport) is WorkflowRunnerHttpTransport
            and self.transport.request is None
            and not {"health_check", "submit", "_request"}.intersection(vars(self.transport))
        ):
            return self._transport_call(
                self.transport.submit_when_healthy,
                payload,
                monotonic_deadline=monotonic_deadline,
            )
        if not self.is_healthy(monotonic_deadline=monotonic_deadline):
            raise RunnerUnavailableError("Workflow Runner is unavailable")
        return self._transport_call(
            self.transport.submit,
            payload,
            monotonic_deadline=monotonic_deadline,
        )

    def status(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._transport_call(
            self.transport.status,
            run_id,
            monotonic_deadline=monotonic_deadline,
        )

    def terminate(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._transport_call(
            self.transport.terminate,
            run_id,
            monotonic_deadline=monotonic_deadline,
        )

    def cleanup(
        self, run_id: str, *, monotonic_deadline: float | None = None
    ) -> dict[str, Any]:
        return self._transport_call(
            self.transport.cleanup,
            run_id,
            monotonic_deadline=monotonic_deadline,
        )

    @staticmethod
    def _transport_call(
        operation,
        argument,
        *,
        monotonic_deadline: float | None,
    ) -> dict[str, Any]:
        try:
            result = operation(
                argument,
                monotonic_deadline=monotonic_deadline,
            )
        except Exception as exc:
            if isinstance(exc, RunnerDeadlineExceededError) or (
                isinstance(exc, (TimeoutError, httpx.TimeoutException))
                and monotonic_deadline is not None
                and time.monotonic() >= monotonic_deadline
            ) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code == 504
            ):
                raise RunnerDeadlineExceededError(
                    "Workflow Runner deadline expired"
                ) from exc
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
