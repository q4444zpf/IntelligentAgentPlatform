from app.runtime.sandbox_inspector import SandboxInspector


def test_inspector_derives_readiness_from_container_config():
    inspector = SandboxInspector()
    readiness = inspector.inspect({
        "HostConfig": {
            "NetworkMode": "intelligent-agent-platform_runner-gateway",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "Memory": 536870912,
            "PidsLimit": 128,
            "NanoCpus": 1000000000,
        },
        "NetworkSettings": {
            "Networks": {"intelligent-agent-platform_runner-gateway": {}},
        },
        "Mounts": [{"Source": "/workspace/run-1", "Destination": "/workspace"}],
        "Config": {
            "Image": "iap/workflow-runner:v1",
            "User": "65534",
            "ReadonlyRootfs": True,
            "Env": [
                "IAP_RUN_EXECUTION_REQUEST={}",
                "IAP_RUNNER_GATEWAY_URL=http://api:8000/internal/runner",
            ],
        },
        "Labels": {"iap.cleanup_guaranteed": "true"},
    })

    assert readiness.is_ready() is True


def test_inspector_reports_real_missing_controls():
    readiness = SandboxInspector().inspect({"Config": {"Image": "ubuntu:latest"}, "HostConfig": {}})

    assert readiness.is_ready() is False
    assert set(readiness.missing()) == {
        "image_trusted", "non_root", "read_only_root", "runner_gateway_network",
        "resource_limits", "cleanup_guaranteed", "non_privileged", "capabilities_dropped",
        "environment_allowlisted", "mounts_allowlisted",
    }


def test_inspector_reads_real_docker_attrs_layout():
    readiness = SandboxInspector().inspect({
        "Config": {
            "Image": "iap/workflow-runner:v1",
            "User": "65534",
            "Labels": {"iap.cleanup_guaranteed": "true"},
            "Env": [
                "IAP_RUN_EXECUTION_REQUEST={}",
                "IAP_RUNNER_GATEWAY_URL=http://api:8000/internal/runner",
            ],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "intelligent-agent-platform_runner-gateway",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "Memory": 1,
            "PidsLimit": 1,
            "NanoCpus": 1,
        },
        "NetworkSettings": {
            "Networks": {"intelligent-agent-platform_runner-gateway": {}},
        },
        "Mounts": [{"Source": "/workspace/run-1", "Destination": "/workspace"}],
    })
    assert readiness.is_ready() is True


def test_inspector_rejects_extra_network_secret_environment_and_docker_socket():
    readiness = SandboxInspector().inspect({
        "Config": {
            "Image": "iap/workflow-runner:v1",
            "User": "65534",
            "ReadonlyRootfs": True,
            "Labels": {"iap.cleanup_guaranteed": "true"},
            "Env": [
                "IAP_RUN_EXECUTION_REQUEST={}",
                "IAP_RUNNER_GATEWAY_URL=http://api:8000/internal/runner",
                "DATABASE_URL=secret",
            ],
        },
        "HostConfig": {
            "NetworkMode": "intelligent-agent-platform_runner-gateway",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "Memory": 1,
            "PidsLimit": 1,
            "NanoCpus": 1,
            "Binds": ["/var/run/docker.sock:/var/run/docker.sock"],
        },
        "NetworkSettings": {
            "Networks": {
                "intelligent-agent-platform_runner-gateway": {},
                "bridge": {},
            },
        },
        "Mounts": [
            {"Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
        ],
    })

    assert readiness.is_ready() is False
    assert {
        "runner_gateway_network",
        "docker_socket_absent",
        "environment_allowlisted",
    } <= set(readiness.missing())
