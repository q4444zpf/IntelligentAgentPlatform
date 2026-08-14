from __future__ import annotations

import os
from typing import Any

from .container_launcher import ControlledContainerLauncher
from .container_policy import ContainerPolicy
from .launcher_api import create_launcher_app


def create_launcher_service(*, client: Any, runner_token: str | None = None):
    token = runner_token if runner_token is not None else os.getenv("IAP_RUNNER_LAUNCHER_TOKEN", "")
    if not token:
        raise RuntimeError("runner launcher token is not configured")
    image = os.getenv("IAP_SANDBOX_RUNNER_IMAGE", "iap/workflow-runner:latest")
    network = os.getenv(
        "IAP_RUNNER_GATEWAY_NETWORK",
        "intelligent-agent-platform_runner-gateway",
    )
    gateway_url = os.getenv(
        "IAP_RUNNER_GATEWAY_URL",
        "http://api:8000/internal/runner",
    )
    launcher = ControlledContainerLauncher(
        client,
        ContainerPolicy(image, network=network, gateway_url=gateway_url),
    )
    app = create_launcher_app(launcher, runner_token=token)
    app.state.runner_image = image
    return app


def create_docker_launcher_service():
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError("docker SDK is required by launcher service") from exc
    return create_launcher_service(client=docker.from_env())


app = None
if os.getenv("IAP_LAUNCHER_SERVICE_ENABLED", "false").lower() in {"1", "true", "yes"}:
    app = create_docker_launcher_service()
