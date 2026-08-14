from app.runtime.sandbox_readiness import SandboxReadiness


def test_readiness_requires_all_security_controls():
    ready = SandboxReadiness(
        image_trusted=True,
        non_root=True,
        read_only_root=True,
        runner_gateway_network=True,
        resource_limits=True,
        cleanup_guaranteed=True,
    )
    assert ready.is_ready() is True


def test_readiness_reports_missing_controls():
    ready = SandboxReadiness(
        image_trusted=True,
        non_root=True,
        read_only_root=True,
        runner_gateway_network=False,
        resource_limits=True,
        cleanup_guaranteed=True,
    )
    assert ready.is_ready() is False
    assert ready.missing() == ["runner_gateway_network"]
