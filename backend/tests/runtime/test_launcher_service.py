import pytest

from app.runtime.launcher_service import create_launcher_service


def test_launcher_service_requires_configured_runner_token(monkeypatch):
    monkeypatch.delenv("IAP_RUNNER_LAUNCHER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="token"):
        create_launcher_service(client=object())


def test_launcher_service_wraps_docker_sdk_client(monkeypatch):
    monkeypatch.setenv("IAP_RUNNER_LAUNCHER_TOKEN", "secret")

    class FakeDocker:
        def __init__(self):
            self.containers = self

        def run(self, **kwargs):
            return kwargs

    app = create_launcher_service(client=FakeDocker())
    assert app.title == "Sandbox Launcher"


def test_launcher_service_uses_configured_trusted_image(monkeypatch):
    monkeypatch.setenv("IAP_RUNNER_LAUNCHER_TOKEN", "secret")
    monkeypatch.setenv("IAP_SANDBOX_RUNNER_IMAGE", "iap/workflow-runner:test")

    class FakeDocker:
        containers = object()

    service = create_launcher_service(client=FakeDocker())
    assert service.state.runner_image == "iap/workflow-runner:test"


def test_launcher_module_does_not_enable_service_without_explicit_flag(monkeypatch):
    monkeypatch.delenv("IAP_LAUNCHER_SERVICE_ENABLED", raising=False)
    import importlib
    module = importlib.import_module("app.runtime.launcher_service")
    assert module.app is None
