from __future__ import annotations

import json
import os
import sys

from pydantic import ValidationError

from .execution_contract import RunExecutionRequest
from .runner_gateway_client import RunnerGatewayClient
from .sandbox_runtime import SandboxRuntime


def load_execution_request() -> RunExecutionRequest:
    payload = json.loads(os.environ["IAP_RUN_EXECUTION_REQUEST"])
    return RunExecutionRequest.model_validate(payload)


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    try:
        request = load_execution_request()
    except (KeyError, json.JSONDecodeError, ValidationError):
        return 2
    result = SandboxRuntime(
        RunnerGatewayClient.from_execution_request(request)
    ).execute(request)
    if result.status in {"completed", "interrupted"}:
        return 0
    if result.status == "cancelled":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
