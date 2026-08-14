from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath


class InvalidContainerPolicyError(ValueError):
    pass


RUNNER_GATEWAY_NETWORK = "intelligent-agent-platform_runner-gateway"
RUNNER_GATEWAY_URL = "http://api:8000/internal/runner"
RUNNER_ENVIRONMENT_KEYS = {
    "IAP_RUN_EXECUTION_REQUEST",
    "IAP_RUNNER_GATEWAY_URL",
}
RUNNER_BASE_ENVIRONMENT_KEYS = {
    "GPG_KEY",
    "LANG",
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PYTHON_SHA256",
    "PYTHON_VERSION",
}


@dataclass(frozen=True)
class ContainerPolicy:
    image: str
    mem_limit: str = "512m"
    pids_limit: int = 128
    cpus: float = 1.0
    network: str = RUNNER_GATEWAY_NETWORK
    gateway_url: str = RUNNER_GATEWAY_URL

    def __post_init__(self):
        if not self.image.startswith("iap/") or ":" not in self.image:
            raise InvalidContainerPolicyError("untrusted runner image")
        if self.pids_limit <= 0 or self.cpus <= 0:
            raise InvalidContainerPolicyError("invalid resource limits")
        if self.network != RUNNER_GATEWAY_NETWORK:
            raise InvalidContainerPolicyError("invalid runner gateway network")
        if self.gateway_url != RUNNER_GATEWAY_URL:
            raise InvalidContainerPolicyError("invalid runner gateway URL")

    def build(self, run_id: str, workspace_path: str, *, execution_request: str | None = None) -> dict:
        if not run_id or "/" in run_id or "\\" in run_id or ".." in PurePosixPath(run_id).parts:
            raise InvalidContainerPolicyError("invalid run id")
        path = PurePosixPath(workspace_path)
        if not path.is_absolute() or path != PurePosixPath("/workspace") / run_id:
            raise InvalidContainerPolicyError("workspace path must be an absolute run directory")
        environment = None
        if execution_request is not None:
            if len(execution_request.encode("utf-8")) > 8192:
                raise InvalidContainerPolicyError("execution request is too large")
            try:
                parsed = json.loads(execution_request)
            except (TypeError, ValueError) as exc:
                raise InvalidContainerPolicyError("execution request must be JSON") from exc
            if not isinstance(parsed, dict):
                raise InvalidContainerPolicyError("execution request must be an object")
            environment = {
                "IAP_RUN_EXECUTION_REQUEST": execution_request,
                "IAP_RUNNER_GATEWAY_URL": self.gateway_url,
            }
        return {
            "image": self.image,
            "name": f"iap-run-{run_id}",
            "command": [
                "python",
                "-m",
                "app.runtime.run_worker",
            ],
            "network": self.network,
            "read_only": True,
            "privileged": False,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": self.mem_limit,
            "pids_limit": self.pids_limit,
            "nano_cpus": int(self.cpus * 1_000_000_000),
            "labels": {"iap.cleanup_guaranteed": "true"},
            "volumes": {str(path): {"bind": "/workspace", "mode": "rw"}},
            **({"environment": environment} if environment else {}),
        }
