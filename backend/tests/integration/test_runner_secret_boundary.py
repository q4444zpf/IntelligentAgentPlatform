from app.runtime.container_policy import ContainerPolicy


def test_run_container_environment_contains_only_execution_gateway_settings():
    config = ContainerPolicy("iap/workflow-runner:latest").build(
        "run-1",
        "/workspace/run-1",
        execution_request='{"run_id":"run-1"}',
    )

    assert set(config["environment"]) == {
        "IAP_RUN_EXECUTION_REQUEST",
        "IAP_RUNNER_GATEWAY_URL",
    }
    forbidden = {
        "DATABASE_URL",
        "IAP_OBJECT_STORAGE_SECRET_KEY",
        "OPENAI_API_KEY",
        "MCP_TOKEN",
        "DOCKER_HOST",
        "IAP_RUNNER_TOKEN_SIGNING_KEY",
        "IAP_RUNNER_LAUNCHER_TOKEN",
    }
    assert forbidden.isdisjoint(config["environment"])
    assert config["network"] == "intelligent-agent-platform_runner-gateway"
    assert list(config["volumes"]) == ["/workspace/run-1"]
