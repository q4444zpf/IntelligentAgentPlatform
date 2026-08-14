import pytest

from app.runtime.container_policy import ContainerPolicy, InvalidContainerPolicyError


def test_policy_builds_non_privileged_runner_gateway_container_config():
    policy = ContainerPolicy(image="iap/workflow-runner:latest")

    request_json = '{"run_id":"run-1"}'
    config = policy.build("run-1", "/workspace/run-1", execution_request=request_json)

    assert config["image"] == "iap/workflow-runner:latest"
    assert config["name"] == "iap-run-run-1"
    assert config["network"] == "intelligent-agent-platform_runner-gateway"
    assert config["read_only"] is True
    assert config["privileged"] is False
    assert config["cap_drop"] == ["ALL"]
    assert config["mem_limit"] == "512m"
    assert config["pids_limit"] == 128
    assert config["labels"] == {"iap.cleanup_guaranteed": "true"}
    assert config["volumes"] == {"/workspace/run-1": {"bind": "/workspace", "mode": "rw"}}
    assert config["environment"] == {
        "IAP_RUN_EXECUTION_REQUEST": request_json,
        "IAP_RUNNER_GATEWAY_URL": "http://api:8000/internal/runner",
    }
    assert config["command"] == [
        "python",
        "-m",
        "app.runtime.run_worker",
    ]
    assert config["read_only"] is True


def test_policy_rejects_non_internal_runner_gateway_identity():
    with pytest.raises(InvalidContainerPolicyError, match="network"):
        ContainerPolicy(
            image="iap/workflow-runner:latest",
            network="bridge",
        )


def test_policy_rejects_untrusted_image_and_path():
    with pytest.raises(InvalidContainerPolicyError):
        ContainerPolicy(image="ubuntu:latest")

    policy = ContainerPolicy(image="iap/workflow-runner:latest")
    with pytest.raises(InvalidContainerPolicyError):
        policy.build("../escape", "/tmp/escape")


def test_policy_requires_a_bounded_json_execution_request():
    policy = ContainerPolicy(image="iap/workflow-runner:latest")

    with pytest.raises(InvalidContainerPolicyError):
        policy.build("run-1", "/workspace/run-1", execution_request="not-json")
    with pytest.raises(InvalidContainerPolicyError):
        policy.build("run-1", "/workspace/run-1", execution_request="{}" * 5000)


@pytest.mark.parametrize("workspace", ["/workspace/other-run", "/workspace/run-1/../other-run", "/tmp/run-1"])
def test_policy_rejects_workspace_outside_run_root(workspace):
    policy = ContainerPolicy(image="iap/workflow-runner:latest")
    with pytest.raises(InvalidContainerPolicyError):
        policy.build("run-1", workspace)
