import pytest

from app.runtime.container_launcher import (
    ContainerLauncher,
    ControlledContainerLauncher,
    LauncherUnavailableError,
)
from app.runtime.container_policy import ContainerPolicy


class FakeContainer:
    def __init__(self, attrs=None): self.removed = False; self.killed = False; self.reloaded = False; self.attrs = attrs or {}; self.status = "running"
    def wait(self, timeout=None): return {"StatusCode": 0}
    def remove(self, force=False): self.removed = force
    def kill(self): self.killed = True
    def reload(self): self.reloaded = True


class FailingWaitContainer(FakeContainer):
    def wait(self, timeout=None):
        raise TimeoutError("timed out")


class FakeClient:
    def __init__(self):
        self.kwargs = None
        self.container = FakeContainer({
            "Config": {"Image": "iap/workflow-runner:latest", "User": "65534", "ReadonlyRootfs": True},
            "HostConfig": {"NetworkMode": "none", "Privileged": False, "CapDrop": ["ALL"], "Memory": 1, "PidsLimit": 1, "NanoCpus": 1},
            "Labels": {"iap.cleanup_guaranteed": "true"},
        })
    def containers_run(self, **kwargs): self.kwargs = kwargs; return self.container


def execution_payload(run_id="run-1"):
    return {
        "workspace_path": f"/workspace/{run_id}",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }


def test_launcher_runs_with_policy_and_always_removes_container(tmp_path):
    client = FakeClient()
    launcher = ContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    result = launcher.run("run-1", "/workspace/run-1")

    assert result == {"StatusCode": 0}
    assert client.kwargs["network_disabled"] is True
    assert client.kwargs["remove"] is False
    assert client.container.removed is True


def test_launcher_fails_when_docker_client_is_missing():
    launcher = ContainerLauncher(None, ContainerPolicy("iap/workflow-runner:latest"))

    with pytest.raises(LauncherUnavailableError):
        launcher.run("run-1", "/workspace/run-1")


def test_launcher_cleans_container_when_wait_times_out():
    client = FakeClient()
    client.container = FailingWaitContainer(client.container.attrs)
    launcher = ContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    with pytest.raises(TimeoutError):
        launcher.run("run-timeout", "/workspace/run-timeout")
    assert client.container.removed is True


def test_controlled_launcher_scopes_container_lifecycle_to_run():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    created = launcher.create("run-1", execution_payload())
    assert created["status"] == "created"
    assert launcher.inspect("run-1")["status"] == "running"
    assert launcher.terminate("run-1")["status"] == "terminated"
    assert launcher.cleanup("run-1")["status"] == "cleaned"

    with pytest.raises(LauncherUnavailableError):
        launcher.inspect("run-1")
    assert launcher.cleanup("run-1")["status"] == "cleaned"

    with pytest.raises(LauncherUnavailableError):
        launcher.cleanup("never-created")


def test_controlled_launcher_passes_only_validated_request_references():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    launcher.create("run-1", {
        "workspace_path": "/workspace/run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    })

    request = client.kwargs["environment"]["IAP_RUN_EXECUTION_REQUEST"]
    assert '"run_id":"run-1"' in request
    assert '"agent_version":"agent-v1"' in request
    assert "workspace_path" not in request
    assert '"run_token":"secret-token"' in request
    assert "secret-token" not in str(launcher.inspect("run-1"))


def test_controlled_launcher_reports_sanitized_exit_and_oom_state():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())
    client.container.attrs["State"] = {
        "Status": "exited",
        "Running": False,
        "ExitCode": 137,
        "OOMKilled": True,
        "Error": "secret host path /var/lib/docker",
    }

    assert launcher.inspect("run-1") == {
        "run_id": "run-1",
        "container_id": None,
        "status": "exited",
        "exit_code": 137,
        "oom_killed": True,
    }


def test_controlled_launcher_cleanup_is_idempotent_after_success():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())

    assert launcher.cleanup("run-1")["status"] == "cleaned"
    assert launcher.cleanup("run-1") == {"run_id": "run-1", "status": "cleaned"}


def test_controlled_launcher_terminate_is_idempotent_for_existing_container():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())

    launcher.terminate("run-1")
    launcher.terminate("run-1")

    assert client.container.killed is True


def test_controlled_launcher_inspects_container_before_accepting_execution():
    client = FakeClient()
    client.container.attrs = {"Config": {"Image": "ubuntu:latest"}, "HostConfig": {}}
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    with pytest.raises(LauncherUnavailableError, match="image_trusted"):
        launcher.create("run-unsafe", execution_payload("run-unsafe"))
    assert client.container.removed is True


def test_controlled_launcher_rejects_duplicate_run_without_replacing_container():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())

    with pytest.raises(LauncherUnavailableError, match="already exists"):
        launcher.create("run-1", execution_payload())


def test_controlled_launcher_termination_forces_stop():
    client = FakeClient()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))
    launcher.create("run-1", execution_payload())

    launcher.terminate("run-1")

    assert client.container.killed is True


def test_controlled_launcher_supports_docker_sdk_containers_run():
    inner = FakeClient()

    class Containers:
        def run(self, **kwargs):
            inner.kwargs = kwargs
            return inner.container

    class DockerSdkClient:
        containers = Containers()

    launcher = ControlledContainerLauncher(DockerSdkClient(), ContainerPolicy("iap/workflow-runner:latest"))
    assert launcher.create("run-sdk", execution_payload("run-sdk"))["status"] == "created"
    assert inner.container.reloaded is True


def test_controlled_launcher_rediscovers_run_container_after_service_restart():
    client = FakeClient()

    class Containers:
        @staticmethod
        def get(name):
            assert name == "iap-run-run-1"
            return client.container

    client.containers = Containers()
    launcher = ControlledContainerLauncher(client, ContainerPolicy("iap/workflow-runner:latest"))

    assert launcher.inspect("run-1")["status"] == "running"
    assert launcher.terminate("run-1")["status"] == "terminated"
    assert launcher.cleanup("run-1")["status"] == "cleaned"
