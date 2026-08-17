from app.runtime.docker_inspect import DockerInspectTransport


class FakeContainer:
    attrs = {
        "Config": {"Image": "iap/workflow-runner:v1", "User": "65534", "ReadonlyRootfs": True},
        "HostConfig": {"NetworkMode": "none", "Privileged": False, "CapDrop": ["ALL"], "Memory": 1, "PidsLimit": 1, "NanoCpus": 1},
        "Config": {"Image": "iap/workflow-runner:v1", "User": "65534", "ReadonlyRootfs": True},
    }


class FakeContainers:
    def get(self, name):
        assert name == "iap-run-run-1"
        return FakeContainer()


class FakeDocker:
    containers = FakeContainers()


def test_transport_reads_container_attrs_without_mutation():
    transport = DockerInspectTransport(FakeDocker())

    info = transport.inspect("iap-run-run-1")

    assert info["Config"]["Image"] == "iap/workflow-runner:v1"


def test_transport_returns_none_when_container_is_missing():
    class Missing:
        class containers:
            @staticmethod
            def get(_name):
                raise RuntimeError("not found")

    assert DockerInspectTransport(Missing()).inspect("missing") is None
