from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from app.mcp.health_service import McpHealthService
from app.mcp.protocol import McpProtocolClient
from app.mcp.store import McpStore


class McpHealthScheduler:
    def __init__(self, client_keys: Iterable[str] | Callable[[], Iterable[str]], check: Callable[[str], Awaitable[None]], *, interval_seconds: float = 300.0):
        self._client_keys = client_keys
        self._check = check
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self.cancelled = False

    async def run_once(self) -> None:
        keys = self._client_keys() if callable(self._client_keys) else self._client_keys
        for client_key in keys:
            if self.cancelled:
                return
            await self._check(client_key)

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                await self.run_once()
        except asyncio.CancelledError:
            raise

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.cancelled = False
            self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        self.cancelled = True
        if self._task is not None:
            self._task.cancel()

    async def wait_closed(self) -> None:
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class DefaultMcpHealthRunner:
    def __init__(self, store: McpStore | None = None, health: McpHealthService | None = None, protocol: McpProtocolClient | None = None):
        self.store = store or McpStore()
        self.health = health or McpHealthService(self.store.session_factory)
        self.protocol = protocol or McpProtocolClient()

    def client_keys(self) -> list[str]:
        return [record["key"] for record in self.store.list() if record.get("enabled") and record.get("status", "active") == "active" and record.get("transport") in {"streamable_http", "sse"}]

    async def check(self, client_key: str) -> None:
        await asyncio.to_thread(self._check_sync, client_key)

    def _check_sync(self, client_key: str) -> None:
        with self.store.session_factory.begin() as session:
            if not self.health.is_due(session, client_key) or not self.health.acquire_lease(session, client_key):
                return
        record = self.store.get(client_key)
        if record is None:
            return
        try:
            if record.get("credential_id"):
                raise RuntimeError("credential reference requires runtime resolver")
            self.protocol.list_tools(record["url"], record["transport"], record["headers"])
            with self.store.session_factory.begin() as session:
                self.health.record_result(session, client_key, ok=True, phase="tools/list", latency_ms=None)
        except Exception:
            with self.store.session_factory.begin() as session:
                row = self.health.record_result(session, client_key, ok=False, phase="initialize", error_code="CONNECTION_FAILED", error_message="remote MCP health check failed")
                if row.health_status == "offline":
                    self.health.mark_source_unavailable(session, client_key)


default_mcp_health_runner = DefaultMcpHealthRunner()
default_mcp_health_scheduler = McpHealthScheduler(default_mcp_health_runner.client_keys, default_mcp_health_runner.check)
