from pathlib import Path

import yaml


def test_runner_has_no_docker_socket_and_launcher_is_sandbox_profile():
    compose = Path(__file__).parents[3].joinpath("compose.yaml").read_text(encoding="utf-8")
    runner = compose.split("  workflow-runner:", 1)[1].split("\n  sandbox-launcher:", 1)[0]
    launcher = compose.split("  sandbox-launcher:", 1)[1]
    assert "/var/run/docker.sock" not in runner
    assert "profiles:" in launcher and "sandbox" in launcher
    assert "/var/run/docker.sock:/var/run/docker.sock" in launcher


def test_compose_declares_dedicated_internal_runner_gateway_network():
    compose = yaml.safe_load(
        Path(__file__).parents[3].joinpath("compose.yaml").read_text(encoding="utf-8")
    )

    network = compose["networks"]["runner-gateway"]
    assert network == {
        "name": "intelligent-agent-platform_runner-gateway",
        "internal": True,
    }
    assert "runner-gateway" in compose["services"]["api"]["networks"]
    assert compose["services"]["workflow-runner"]["networks"] == ["runner-gateway"]
    assert compose["services"]["sandbox-launcher"]["networks"] == ["runner-gateway"]
