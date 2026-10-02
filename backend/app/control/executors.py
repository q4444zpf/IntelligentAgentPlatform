"""Configuration-backed physical control adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .service import ControlExecutor, ControlExecutorResult


MAX_BODY_BYTES = 64 * 1024
_ASSET_TYPE = re.compile(r"^[a-z][a-z0-9_]*$")
_TOKEN_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_SUMMARY_VALUE = re.compile(r"^[A-Za-z0-9 _.:/-]{0,80}$")
_SUMMARY_KEYS = frozenset({"status", "state", "code", "reason_code"})


@dataclass(frozen=True)
class ControlExecutorEndpoint:
    asset_type: str
    base_url: str
    token_env: str
    timeout_seconds: float


@dataclass(frozen=True)
class ControlReadiness:
    ready: bool
    unavailable_asset_types: tuple[str, ...]


class HttpControlExecutor:
    def __init__(self, endpoint: ControlExecutorEndpoint, token: str, client: httpx.Client):
        self.endpoint = endpoint
        self._token = token
        self._client = client

    def execute(self, command, external_idempotency_key: str) -> ControlExecutorResult:
        return self._send("execute", command, command.parameters, external_idempotency_key)

    def rollback(self, command, rollback_plan: dict, external_idempotency_key: str) -> ControlExecutorResult:
        return self._send("rollback", command, rollback_plan, external_idempotency_key)

    def _send(self, kind: str, command, operation_data: dict, external_idempotency_key: str) -> ControlExecutorResult:
        body = {
            "command_id": command.id,
            "project_id": command.project_id,
            "policy_digest": command.policy_digest,
            "parameter_digest": command.parameter_digest,
            "target": command.target,
            "parameters" if kind == "execute" else "rollback_plan": operation_data,
            "simulation_digest": command.simulation_digest,
            "valid_until": command.valid_until.isoformat(),
        }
        try:
            encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("executor request is invalid") from None
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("executor request exceeds size limit")
        try:
            with self._client.stream(
                "POST", f"{self.endpoint.base_url}/commands/{kind}", content=encoded,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Idempotency-Key": external_idempotency_key,
                    "Content-Type": "application/json",
                },
                timeout=self.endpoint.timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise ValueError("executor response is unavailable")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_BODY_BYTES:
                        raise ValueError("executor response exceeds size limit")
        except httpx.HTTPError:
            raise ValueError("executor response is unavailable") from None
        try:
            parsed = json.loads(payload)
            result = ControlExecutorResult.model_validate(parsed)
        except (ValueError, TypeError, ValidationError):
            raise ValueError("executor response is invalid") from None
        summary = result.summary or {}
        safe_summary = {
            key: value for key, value in summary.items()
            if key in _SUMMARY_KEYS and isinstance(value, str) and _SAFE_SUMMARY_VALUE.fullmatch(value)
        }
        return ControlExecutorResult(outcome=result.outcome, external_id=result.external_id, summary=safe_summary)

    def healthy(self) -> bool:
        try:
            with self._client.stream(
                "GET", f"{self.endpoint.base_url}/health", timeout=2.0,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as response:
                return response.status_code == 200
        except httpx.HTTPError:
            return False


@dataclass(frozen=True)
class ControlExecutorRegistry:
    executors: Mapping[str, ControlExecutor] = field(default_factory=lambda: MappingProxyType({}))
    enabled: bool = False

    @classmethod
    def from_json(
        cls, value: str | None, secret_reader: Callable[[str], str | None],
        environment: str, http_transport: httpx.BaseTransport | None = None,
        *, enabled: bool = False,
    ) -> ControlExecutorRegistry:
        if value is None or not value.strip():
            return cls(enabled=enabled)

        def unique_pairs(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate control executor configuration key")
                result[key] = item
            return result

        try:
            configuration = json.loads(value, object_pairs_hook=unique_pairs)
        except (json.JSONDecodeError, TypeError):
            raise ValueError("invalid control executor configuration") from None
        if not isinstance(configuration, dict):
            raise ValueError("control executor configuration must be an object")
        executors = {}
        for asset_type, raw in configuration.items():
            if not isinstance(asset_type, str) or not _ASSET_TYPE.fullmatch(asset_type):
                raise ValueError("invalid control executor asset type")
            if not isinstance(raw, dict) or set(raw) != {"base_url", "token_env", "timeout_seconds"}:
                raise ValueError("invalid control executor endpoint keys")
            base_url, token_env, timeout = raw["base_url"], raw["token_env"], raw["timeout_seconds"]
            if not isinstance(base_url, str) or not base_url or base_url != base_url.strip():
                raise ValueError("invalid control executor URL")
            try:
                url = urlsplit(base_url)
                port = url.port
            except ValueError:
                raise ValueError("invalid control executor URL") from None
            if (url.scheme not in {"http", "https"} or not url.hostname or port == 0
                    or url.username or url.password or url.query or url.fragment or base_url.endswith("/")):
                raise ValueError("invalid control executor URL")
            if environment == "production" and url.scheme != "https":
                raise ValueError("production control executor requires HTTPS")
            if not isinstance(token_env, str) or not _TOKEN_ENV.fullmatch(token_env):
                raise ValueError("invalid control executor token environment name")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
                raise ValueError("control executor timeout must be 1..30 seconds")
            try:
                token = secret_reader(token_env)
            except ValueError:
                raise ValueError("control executor secret is unavailable") from None
            if not token or not token.strip() or "\r" in token or "\n" in token:
                raise ValueError("control executor secret is unavailable")
            endpoint = ControlExecutorEndpoint(asset_type, base_url, token_env, timeout)
            client = httpx.Client(transport=http_transport, trust_env=False, follow_redirects=False)
            executors[asset_type] = HttpControlExecutor(endpoint, token, client)
        return cls(MappingProxyType(executors), enabled=enabled)

    def validate_startup(self) -> None:
        if self.enabled and not self.executors:
            raise ValueError("control gateway requires executor configuration")

    def readiness(self) -> ControlReadiness:
        if not self.enabled:
            return ControlReadiness(True, ())
        unavailable = tuple(sorted(asset_type for asset_type, executor in self.executors.items() if not executor.healthy()))
        return ControlReadiness(not unavailable and bool(self.executors), unavailable)
