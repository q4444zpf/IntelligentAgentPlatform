from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, TypeVar

T = TypeVar("T")


class SandboxDisabledError(RuntimeError):
    pass


class SandboxTimeoutError(RuntimeError):
    pass


@dataclass
class SandboxExecutor:
    root_dir: str | Path
    enabled: bool = False
    timeout_seconds: float = 30.0
    operations: dict[str, Callable[[Path], object]] | None = None

    def run_registered(self, run_id: str, operation_name: str):
        operations = self.operations or {}
        operation = operations.get(operation_name)
        if operation is None:
            raise KeyError(operation_name)
        return self.run(run_id, operation)

    def run(self, run_id: str, operation: Callable[[Path], T]) -> T:
        if not self.enabled:
            raise SandboxDisabledError("Sandbox Executor is not enabled")
        root = Path(self.root_dir)
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=f"{run_id}-", dir=root) as workspace:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"sandbox-{run_id}") as pool:
                future = pool.submit(operation, Path(workspace))
                try:
                    return future.result(timeout=self.timeout_seconds)
                except FutureTimeoutError as error:
                    future.cancel()
                    raise SandboxTimeoutError("Sandbox operation timed out") from error
