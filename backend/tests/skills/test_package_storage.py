from __future__ import annotations

import io
import zipfile
from dataclasses import FrozenInstanceError

import pytest

from app.skills.package import MAX_ZIP_BYTES, parse_skill_bundle
from app.skills.package_storage import (
    SkillPackageStorage,
    SkillPackageStorageError,
    StoredSkillPackage,
)


def valid_manifest() -> bytes:
    return b"---\nname: s\ndescription: Sample\nversion: '1.0'\n---\nInstructions\n"


def make_bundle(entries: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(path, data)
    return stream.getvalue()


class TrackingBody(io.BytesIO):
    def __init__(self, data: bytes, *, declared_length: int | None = None):
        super().__init__(data)
        self.declared_length = len(data) if declared_length is None else declared_length
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class MemoryS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict[str, object]] = {}
        self.next_body: TrackingBody | None = None
        self.get_calls = 0

    def put_object(self, **request):
        key = (request["Bucket"], request["Key"])
        if key in self.objects:
            raise AssertionError("test fake observed an overwrite")
        body = bytes(request["Body"])
        assert request["ContentLength"] == len(body)
        self.objects[key] = {"Body": body, "Metadata": dict(request["Metadata"])}
        return {"ETag": '"test"'}

    def get_object(self, **request):
        self.get_calls += 1
        record = self.objects[(request["Bucket"], request["Key"])]
        body = self.next_body or TrackingBody(record["Body"])
        self.next_body = None
        return {
            "Body": body,
            "ContentLength": body.declared_length,
            "Metadata": dict(record["Metadata"]),
        }


@pytest.fixture
def memory_s3():
    return MemoryS3()


@pytest.fixture
def package():
    return parse_skill_bundle(
        make_bundle([("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"reference")])
    )[0]


def test_round_trips_verified_package(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")

    stored = storage.put("unit", "project", "skill", package)
    restored = parse_skill_bundle(storage.read(stored))[0]

    assert restored.digest == package.digest
    assert stored.package_digest == package.digest
    assert stored.size_bytes == len(storage.read(stored))
    with pytest.raises(FrozenInstanceError):
        stored.object_key = "changed"


def test_serialization_is_deterministic(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")

    first = storage.put("unit", "project", "skill", package)
    second = storage.put("unit", "project", "skill", package)

    assert first.archive_sha256 == second.archive_sha256
    assert storage.read(first) == storage.read(second)


def test_two_uploads_do_not_overwrite(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")

    first = storage.put("unit", "project", "skill", package)
    second = storage.put("unit", "project", "skill", package)

    assert first.object_key != second.object_key
    assert first.object_key.startswith("unit/project/skill/")


@pytest.mark.parametrize("scope", ["", ".", "..", "has/slash", "has\\slash", " leading"])
def test_rejects_unsafe_scope_segments(memory_s3, package, scope):
    storage = SkillPackageStorage(memory_s3, "test-skills")

    with pytest.raises(SkillPackageStorageError):
        storage.put(scope, "project", "skill", package)

    assert memory_s3.objects == {}


def test_rejects_corrupt_body_and_closes_stream(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)
    body = TrackingBody(b"x" * stored.size_bytes)
    memory_s3.next_body = body

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert body.closed


def test_rejects_declared_length_mismatch_and_closes_without_reading(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)
    body = TrackingBody(storage.read(stored), declared_length=stored.size_bytes + 1)
    memory_s3.next_body = body

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert body.bytes_read == 0
    assert body.closed


def test_rejects_metadata_mismatch(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)
    memory_s3.objects[("test-skills", stored.object_key)]["Metadata"]["package-digest"] = "0" * 64

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)


def test_stops_at_archive_limit_and_closes_stream(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)
    body = TrackingBody(b"x" * (MAX_ZIP_BYTES + 1024), declared_length=stored.size_bytes)
    memory_s3.next_body = body

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert body.bytes_read <= MAX_ZIP_BYTES + 64 * 1024
    assert body.closed


def test_rejects_arbitrary_object_key_without_remote_read(memory_s3):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = StoredSkillPackage("other-bucket-object", "0" * 64, "1" * 64, 1)

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert memory_s3.get_calls == 0
