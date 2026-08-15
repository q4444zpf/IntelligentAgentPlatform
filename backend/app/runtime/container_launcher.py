from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .container_policy import ContainerPolicy
from .execution_contract import RunExecutionRequest
from .sandbox_inspector import SandboxInspector


class LauncherUnavailableError(RuntimeError):
    pass


@dataclass
class ContainerLauncher:
    client: Any
    policy: ContainerPolicy

    def run(self, run_id: str, workspace_path: str) -> dict[str, Any]:
        if self.client is None:
            raise LauncherUnavailableError("Container launcher is unavailable")
        config = self.policy.build(run_id, workspace_path)
        container = self.client.containers_run(**config, detach=True, remove=False)
        try:
            return container.wait(timeout=60)
        finally:
            container.remove(force=True)


@dataclass
class ControlledContainerLauncher:
    """Run-scoped lifecycle adapter used by the isolated launcher service."""

    client: Any
    policy: ContainerPolicy
    _containers: dict[str, Any] | None = None
    _cleaned_run_ids: set[str] | None = None
    inspector: SandboxInspector | None = None

    def __post_init__(self) -> None:
        if self._containers is None:
            self._containers = {}
        if self._cleaned_run_ids is None:
            self._cleaned_run_ids = set()
        if self.inspector is None:
            self.inspector = SandboxInspector()

    def _require(self, run_id: str) -> Any:
        if not self._containers or run_id not in self._containers:
            try:
                container = self.client.containers.get(f"iap-run-{run_id}")
                if hasattr(container, "reload"):
                    container.reload()
                readiness = self.inspector.inspect(getattr(container, "attrs", {}))
                if not readiness.is_ready():
                    raise LauncherUnavailableError("sandbox readiness check failed")
                self._containers[run_id] = container
            except LauncherUnavailableError:
                raise
            except Exception as exc:
                raise LauncherUnavailableError("container is not registered for run") from exc
        return self._containers[run_id]

    def create(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise LauncherUnavailableError("Container launcher is unavailable")
        if self._containers and run_id in self._containers:
            raise LauncherUnavailableError("container already exists for run")
        workspace_path = payload.get("workspace_path")
        request = RunExecutionRequest(
            run_id=run_id,
            agent_version=payload.get("agent_version"),
            checkpoint_key=payload.get("checkpoint_key"),
            deadline_at=payload.get("deadline_at"),
            snapshot_id=payload.get("snapshot_id"),
            snapshot_digest=payload.get("snapshot_digest"),
            gateway_url=payload.get("gateway_url"),
            run_token=payload.get("run_token"),
        )
        config = self.policy.build(
            run_id,
            workspace_path,
            execution_request=request.model_dump_json(),
        )
        try:
            if hasattr(self.client, "containers_run"):
                container = self.client.containers_run(**config, detach=True, remove=False)
            else:
                container = self.client.containers.run(**config, detach=True, remove=False)
            if hasattr(container, "reload"):
                container.reload()
            readiness = self.inspector.inspect(getattr(container, "attrs", {}))
            if not readiness.is_ready():
                container.remove(force=True)
                missing = ",".join(readiness.missing())
                raise LauncherUnavailableError(f"sandbox readiness check failed: {missing}")
        except LauncherUnavailableError:
            raise
        except Exception as exc:
            raise LauncherUnavailableError("container creation failed") from exc
        self._cleaned_run_ids.discard(run_id)
        self._containers[run_id] = container
        return {"run_id": run_id, "container_id": getattr(container, "id", config["name"]), "status": "created"}

    def inspect(self, run_id: str) -> dict[str, Any]:
        container = self._require(run_id)
        if hasattr(container, "reload"):
            container.reload()
        state = getattr(container, "attrs", {}).get("State", {})
        running = bool(state.get("Running", getattr(container, "status", "running") == "running"))
        status = "running" if running else str(state.get("Status", "exited"))
        result = {
            "run_id": run_id,
            "container_id": getattr(container, "id", None),
            "status": status,
        }
        if not running:
            exit_code = state.get("ExitCode")
            result["exit_code"] = exit_code if isinstance(exit_code, int) else None
            result["oom_killed"] = state.get("OOMKilled") is True
        return result

    def terminate(self, run_id: str) -> dict[str, Any]:
        container = self._require(run_id)
        if hasattr(container, "kill"):
            container.kill()
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id: str) -> dict[str, Any]:
        if self._cleaned_run_ids is not None and run_id in self._cleaned_run_ids:
            return {"run_id": run_id, "status": "cleaned"}
        container = self._require(run_id)
        container.remove(force=True)
        del self._containers[run_id]
        self._cleaned_run_ids.add(run_id)
        return {"run_id": run_id, "status": "cleaned"}
