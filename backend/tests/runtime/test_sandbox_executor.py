import time
from pathlib import Path

import pytest

from app.runtime.sandbox_executor import SandboxDisabledError, SandboxExecutor, SandboxTimeoutError


def test_executor_rejects_when_disabled(tmp_path):
    executor = SandboxExecutor(root_dir=tmp_path, enabled=False)

    with pytest.raises(SandboxDisabledError):
        executor.run("run-1", lambda workspace: "ok")


def test_executor_runs_in_run_scoped_workspace_and_cleans_it(tmp_path):
    executor = SandboxExecutor(root_dir=tmp_path, enabled=True)
    observed = {}

    result = executor.run("run-1", lambda workspace: observed.update(path=str(workspace), exists=Path(workspace).is_dir()) or "ok")

    assert result == "ok"
    assert observed["exists"] is True
    assert "run-1" in observed["path"]
    assert not Path(observed["path"]).exists()


def test_executor_enforces_timeout_and_cleans_workspace(tmp_path):
    executor = SandboxExecutor(root_dir=tmp_path, enabled=True, timeout_seconds=0.01)

    with pytest.raises(SandboxTimeoutError):
        executor.run("run-timeout", lambda workspace: time.sleep(0.1))
    assert not any(path.name.startswith("run-timeout-") for path in tmp_path.iterdir())


def test_executor_runs_only_registered_operation(tmp_path):
    executor = SandboxExecutor(root_dir=tmp_path, enabled=True, operations={"healthcheck": lambda workspace: "ok"})

    assert executor.run_registered("run-registered", "healthcheck") == "ok"
    with pytest.raises(KeyError):
        executor.run_registered("run-unknown", "shell")
