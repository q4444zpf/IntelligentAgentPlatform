import pytest

from app.runtime.artifact_backend import ArtifactBackend


class FakeArtifactClient:
    def __init__(self):
        self.files = {}

    def create_artifact(self, **request):
        path = request["path"]
        item = {
            "path": path,
            "artifact_id": f"artifact-{len(self.files) + 1}",
            "size_bytes": len(request["data"]),
            "sha256": request["sha256"],
            "content_type": request["content_type"],
            "data": request["data"],
        }
        self.files[path] = item
        return item

    def list_artifacts(self):
        return list(self.files.values())

    def read_artifact(self, artifact_id):
        return next(item for item in self.files.values() if item["artifact_id"] == artifact_id)


@pytest.mark.parametrize("path", ["../secret", "C:/secret", "\\\\host\\share", "/artifacts/a\x00.txt"])
def test_rejects_unsafe_paths(path):
    result = ArtifactBackend(FakeArtifactClient()).write(path, "data")
    assert result.error == "Artifact path is invalid"


def test_maps_relative_paths_into_artifacts():
    client = FakeArtifactClient()
    result = ArtifactBackend(client).write("acceptance-result.txt", "accepted")
    assert result.error is None
    assert result.path == "/artifacts/acceptance-result.txt"
    assert list(client.files) == ["/artifacts/acceptance-result.txt"]


@pytest.mark.parametrize("root_alias", ["/", "."])
def test_maps_root_aliases_to_artifacts(root_alias):
    backend = ArtifactBackend(FakeArtifactClient())
    backend.write("/artifacts/result.txt", "result")
    listed = backend.ls(root_alias)
    assert listed.error is None
    assert [entry["path"] for entry in listed.entries] == ["/artifacts/result.txt"]


def test_is_create_only():
    backend = ArtifactBackend(FakeArtifactClient())
    backend.write("/artifacts/result.txt", "first")
    assert backend.write("/artifacts/result.txt", "second").error == "Artifact already exists"
    assert backend.edit("/artifacts/result.txt", "first", "second").error == "Artifacts are immutable"
    assert backend.delete("/artifacts/result.txt").error == "Artifact deletion requires platform authorization"
