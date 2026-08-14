from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol


class ArtifactPathError(ValueError):
    pass


class ArtifactAlreadyExistsError(FileExistsError):
    pass


class ArtifactGatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    artifact_id: str
    size_bytes: int
    sha256: str
    content_type: str


class RunnerArtifactClient(Protocol):
    def create_artifact(self, **request: Any) -> dict[str, Any]: ...

    def list_artifacts(self) -> list[dict[str, Any]]: ...

    def read_artifact(self, artifact_id: str) -> dict[str, Any]: ...


class ArtifactBackend:
    def __init__(self, client: RunnerArtifactClient) -> None:
        self.client = client

    def write(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ArtifactFile:
        normalized = _normalize_artifact_path(path, allow_root=False)
        if any(item.path == normalized for item in self.list("/artifacts")):
            raise ArtifactAlreadyExistsError(normalized)
        digest = hashlib.sha256(data).hexdigest()
        response = self.client.create_artifact(
            path=normalized,
            data=data,
            content_type=content_type,
            sha256=digest,
            idempotency_key=f"artifact:{hashlib.sha256(normalized.encode()).hexdigest()}:{digest}",
        )
        return _artifact_file(response)

    def read(self, path: str) -> bytes:
        normalized = _normalize_artifact_path(path, allow_root=False)
        artifact = next(
            (item for item in self.list("/artifacts") if item.path == normalized),
            None,
        )
        if artifact is None:
            raise FileNotFoundError(normalized)
        response = self.client.read_artifact(artifact.artifact_id)
        data = response.get("data")
        if not isinstance(data, bytes):
            raise ArtifactGatewayError("artifact content is unavailable")
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ArtifactGatewayError("artifact content digest mismatch")
        return data

    def list(self, path: str = "/artifacts") -> list[ArtifactFile]:
        normalized = _normalize_artifact_path(path, allow_root=True)
        prefix = normalized.rstrip("/") + "/"
        return [
            item
            for item in (_artifact_file(value) for value in self.client.list_artifacts())
            if item.path == normalized or item.path.startswith(prefix)
        ]


def _artifact_file(value: dict[str, Any]) -> ArtifactFile:
    return ArtifactFile(
        path=str(value["path"]),
        artifact_id=str(value["artifact_id"]),
        size_bytes=int(value["size_bytes"]),
        sha256=str(value["sha256"]),
        content_type=str(value["content_type"]),
    )


def _normalize_artifact_path(path: str, *, allow_root: bool) -> str:
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or len(path) > 266
        or not path.startswith("/artifacts")
    ):
        raise ArtifactPathError("artifact path must be under /artifacts")
    parsed = PurePosixPath(path)
    if parsed.parts[:2] != ("/", "artifacts"):
        raise ArtifactPathError("artifact path must be under /artifacts")
    relative_parts = parsed.parts[2:]
    raw_parts = path.split("/")[2:]
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ArtifactPathError("artifact path is invalid")
    if not allow_root and not relative_parts:
        raise ArtifactPathError("artifact path must name a file")
    return "/artifacts" + (f"/{'/'.join(relative_parts)}" if relative_parts else "")
