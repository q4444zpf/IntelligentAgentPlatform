import hashlib
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.runtime import run_worker, sandbox_runtime
from app.runtime.container_launcher import ControlledContainerLauncher
from app.runtime.container_policy import ContainerPolicy
from app.runtime.execution_snapshot import SnapshotSkill, SnapshotSkillFile, canonical_snapshot_bytes
from app.runtime.sandbox_runtime import SandboxRuntime
from tests.runtime.test_container_launcher import FakeClient, execution_payload
from tests.runtime.test_sandbox_runtime import CompletingGraph, FakeFactory, FakeGateway, _request, _snapshot


@pytest.mark.parametrize("early_return", ["completed", "invalid_snapshot", "expired", "gateway_error"])
def test_runtime_stops_deadline_watcher_on_every_return(monkeypatch, tmp_path, early_return):
    watchers = []
    real_thread = threading.Thread

    def track_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        watchers.append(thread)
        return thread

    monkeypatch.setattr(sandbox_runtime, "Thread", track_thread)
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    request = _request(snapshot)
    if early_return == "invalid_snapshot":
        request = request.model_copy(update={"snapshot_digest": "f" * 64})
    elif early_return == "expired":
        request = request.model_copy(update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)})
    elif early_return == "gateway_error":
        def broken():
            raise OSError("gateway unavailable")
        gateway.get_snapshot = broken
    runtime = SandboxRuntime(gateway, agent_factory=FakeFactory(CompletingGraph()), workspace=tmp_path)
    runtime.execute(request)
    for thread in watchers:
        thread.join(timeout=0.1)
    assert not any(thread.is_alive() for thread in watchers)


def test_reused_runtime_is_not_cancelled_by_previous_deadline(tmp_path):
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    factory = FakeFactory(CompletingGraph())
    runtime = SandboxRuntime(gateway, agent_factory=factory, workspace=tmp_path)
    first_request = _request(snapshot).model_copy(update={"execution_deadline_at": datetime.now(UTC) + timedelta(seconds=0.2)})
    assert runtime.execute(first_request).status == "completed"
    first_event = runtime._cancel_event

    class SecondGraph(CompletingGraph):
        def invoke(self, state, **kwargs):
            time.sleep(0.3)
            assert not runtime._cancel_event.is_set()
            return super().invoke(state, **kwargs)

    factory.graph = SecondGraph()
    assert runtime.execute(_request(snapshot)).status == "completed"
    assert not first_event.is_set()


def test_container_termination_gives_worker_bounded_sigterm_grace():
    client = FakeClient()
    stopped = []
    client.container.stop = lambda *, timeout: stopped.append(timeout)
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())
    launcher.terminate("run-1")
    assert stopped == [1]
    assert client.container.killed is False


def test_worker_sigterm_cancels_live_script_and_restores_handler(monkeypatch, tmp_path):
    source = b"import pathlib, time\npathlib.Path('started').touch()\ntime.sleep(30)\nprint('{}')\n"
    skill = SnapshotSkill(name="forecast", files=(SnapshotSkillFile(path="wait.py", size=len(source), sha256=hashlib.sha256(source).hexdigest()),), metadata={"scripts": [{"name": "wait", "path": "wait.py", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "timeout_seconds": 30}]})
    base = _snapshot()
    payload = base.payload.model_copy(update={"skills": (skill,)})
    snapshot = base.model_copy(update={"payload": payload, "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()})
    request = _request(snapshot)
    completions = []

    class Gateway(FakeGateway):
        def read_skill_file(self, skill_name, path):
            return {"skill_name": skill_name, "path": path, "size": len(source), "sha256": hashlib.sha256(source).hexdigest(), "data": source}

        def execute_script(self, **kwargs):
            return {"lease_id": "lease-1", "script_name": kwargs["script_name"], "status": "leased"}

        def complete_script(self, **kwargs):
            completions.append(kwargs)

    class ScriptFactory:
        def build(self, snapshot, **kwargs):
            script = next(tool for tool in kwargs["tools"] if tool.name == "skill.forecast.script.wait")

            class ScriptGraph:
                def invoke(self, state, **config):
                    script.invoke({})
                    return state

            return ScriptGraph()

    gateway = Gateway(snapshot)
    runtime = SandboxRuntime(gateway, agent_factory=ScriptFactory(), workspace=tmp_path)
    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(run_worker, "load_execution_request", lambda: request)
    monkeypatch.setattr(run_worker.RunnerGatewayClient, "from_execution_request", lambda request: gateway)
    monkeypatch.setattr(run_worker, "SandboxRuntime", lambda gateway: runtime)
    previous = signal.getsignal(signal.SIGTERM)
    # A harmless baseline handler keeps the RED test from terminating pytest.
    signal.signal(signal.SIGTERM, lambda *_: None)
    expected_handler = signal.getsignal(signal.SIGTERM)
    stopped = threading.Event()

    def terminate_after_start():
        marker = tmp_path / "skills" / "forecast" / "started"
        until = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < until:
            if stopped.wait(0.01):
                return
        os.kill(os.getpid(), signal.SIGTERM)
        # Bound the failing baseline without waiting for the 30 second script.
        if not stopped.wait(0.5):
            runtime._cancel_event.set()

    sender = threading.Thread(target=terminate_after_start)
    sender.start()
    try:
        assert run_worker.main() == 3
        assert completions[0]["status"] == "cancelled"
        assert signal.getsignal(signal.SIGTERM) is expected_handler
    finally:
        stopped.set()
        sender.join(timeout=5)
        signal.signal(signal.SIGTERM, previous)
