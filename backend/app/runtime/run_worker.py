from __future__ import annotations

import json
import os
import signal
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
    runtime = SandboxRuntime(
        RunnerGatewayClient.from_execution_request(request)
    )
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: runtime.cancel())
    try:
        result = runtime.execute(request)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    if result.status in {"completed", "interrupted"}:
        return 0
    if result.status == "cancelled":
        return 3
    if result.error_code == "sandbox_timeout":
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
