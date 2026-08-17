from __future__ import annotations

import hashlib
from fnmatch import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data

try:
    from deepagents.backends.protocol import DeleteResult
except ImportError:
    @dataclass(frozen=True)
    class DeleteResult:
        error: str | None = None
        path: str | None = None


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


class ArtifactBackend(BackendProtocol):
    def __init__(self, client: RunnerArtifactClient) -> None:
        self.client = client

    def _create(
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

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            artifact = self._create(
                file_path,
                content.encode("utf-8"),
                "text/plain",
            )
        except ArtifactPathError:
            return WriteResult(error="Artifact path is invalid")
        except ArtifactAlreadyExistsError:
            return WriteResult(error="Artifact already exists")
        return WriteResult(path=artifact.path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        try:
            normalized = _normalize_artifact_path(file_path, allow_root=False)
        except ArtifactPathError:
            return ReadResult(error="Artifact path is invalid")
        artifact = next(
            (item for item in self.list("/artifacts") if item.path == normalized),
            None,
        )
        if artifact is None:
            return ReadResult(error=f"File '{normalized}' not found")
        response = self.client.read_artifact(artifact.artifact_id)
        data = response.get("data")
        if not isinstance(data, bytes):
            return ReadResult(error="Artifact content is unavailable")
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            return ReadResult(error="Artifact content digest mismatch")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(error="Artifact is not UTF-8 text")
        if limit <= 0:
            return ReadResult(file_data=create_file_data(""))
        lines = content.splitlines(keepends=True)
        start = max(0, offset)
        if start >= len(lines) and lines:
            return ReadResult(
                error=f"Line offset {offset} exceeds file length ({len(lines)} lines)"
            )
        selected = "".join(lines[start:start + limit]).replace("\r\n", "\n").replace("\r", "\n")
        return ReadResult(file_data=create_file_data(selected))

    def ls(self, path: str) -> LsResult:
        try:
            normalized = _normalize_artifact_path(path, allow_root=True)
        except ArtifactPathError:
            return LsResult(error="Artifact path is invalid")
        prefix = normalized.rstrip("/") + "/"
        entries: dict[str, dict[str, Any]] = {}
        for item in self.list(normalized):
            relative = item.path[len(prefix):]
            if "/" in relative:
                directory = prefix + relative.split("/", 1)[0] + "/"
                entries[directory] = {
                    "path": directory,
                    "is_dir": True,
                    "size": 0,
                    "modified_at": "",
                }
            else:
                entries[item.path] = {
                    "path": item.path,
                    "is_dir": False,
                    "size": item.size_bytes,
                    "modified_at": "",
                }
        return LsResult(entries=[entries[key] for key in sorted(entries)])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        base = path or "/artifacts"
        try:
            normalized = _normalize_artifact_path(base, allow_root=True)
        except ArtifactPathError:
            return GlobResult(error="Artifact path is invalid")
        matches = [
            {
                "path": item.path,
                "is_dir": False,
                "size": item.size_bytes,
                "modified_at": "",
            }
            for item in self.list(normalized)
            if fnmatch(item.path, pattern)
            or fnmatch(item.path.removeprefix(normalized).lstrip("/"), pattern)
        ]
        return GlobResult(matches=matches)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        base = path or "/artifacts"
        matches = []
        truncated = False
        for item in self.list(base):
            if glob and not fnmatch(item.path, glob):
                continue
            read = self.read(item.path)
            if read.error or read.file_data is None:
                continue
            for line_number, line in enumerate(
                str(read.file_data["content"]).splitlines(), start=1
            ):
                if pattern not in line:
                    continue
                if max_count is not None and len(matches) >= max_count:
                    truncated = True
                    return _grep_result(matches, truncated)
                matches.append(
                    {"path": item.path, "line": line_number, "text": line}
                )
        return _grep_result(matches, truncated)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        del old_string, new_string, replace_all
        return EditResult(error="Artifacts are immutable", path=file_path)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(
            error="Artifact deletion requires platform authorization",
            path=file_path,
        )

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        responses = []
        for path, data in files:
            try:
                self._create(path, data)
            except ArtifactPathError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
            except ArtifactAlreadyExistsError:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            else:
                responses.append(FileUploadResponse(path=path))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        artifacts = {item.path: item for item in self.list("/artifacts")}
        for path in paths:
            artifact = artifacts.get(path)
            if artifact is None:
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
                continue
            response = self.client.read_artifact(artifact.artifact_id)
            data = response.get("data")
            if not isinstance(data, bytes):
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
                continue
            responses.append(FileDownloadResponse(path=path, content=data))
        return responses

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


def _grep_result(matches: list[dict[str, Any]], truncated: bool) -> GrepResult:
    fields = getattr(GrepResult, "__dataclass_fields__", {})
    return GrepResult(
        matches=matches,
        **({"truncated": truncated} if "truncated" in fields else {}),
    )


def _normalize_artifact_path(path: str, *, allow_root: bool) -> str:
    if not path or "\x00" in path or "\\" in path or len(path) > 266:
        raise ArtifactPathError("artifact path must be under /artifacts")
    if path in {"/", "."}:
        if allow_root:
            return "/artifacts"
        raise ArtifactPathError("artifact path must name a file")
    if path == "/artifacts" or path.startswith("/artifacts/"):
        candidate = path
    elif path.startswith("/"):
        candidate = f"/artifacts{path}"
    else:
        if ":" in path:
            raise ArtifactPathError("artifact path must be under /artifacts")
        candidate = f"/artifacts/{path}"
    parsed = PurePosixPath(candidate)
    if parsed.parts[:2] != ("/", "artifacts"):
        raise ArtifactPathError("artifact path must be under /artifacts")
    relative_parts = parsed.parts[2:]
    raw_parts = candidate.split("/")[2:]
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ArtifactPathError("artifact path is invalid")
    if not allow_root and not relative_parts:
        raise ArtifactPathError("artifact path must name a file")
    return "/artifacts" + (f"/{'/'.join(relative_parts)}" if relative_parts else "")
