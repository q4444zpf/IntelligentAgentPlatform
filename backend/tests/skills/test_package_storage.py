from __future__ import annotations

import io
import random
import zipfile
from dataclasses import FrozenInstanceError

import pytest
from app.skills import package_storage
from app.skills.package import MAX_ZIP_BYTES, parse_skill_bundle
from app.skills.package_storage import (
    STORAGE_CLIENT_CONFIG,
    SkillPackageStorage,
    SkillPackageStorageError,
    StoredSkillPackage,
    create_default_skill_package_storage,
)


def valid_manifest() -> bytes:
    return b"---\nname: s\ndescription: Sample\nversion: '1.0'\n---\nInstructions\n"


def make_bundle(
    entries: list[tuple[str, bytes]], *, compression: int = zipfile.ZIP_DEFLATED
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
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
        self.last_body: TrackingBody | None = None
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
        self.last_body = body
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


def test_successful_read_closes_stream(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)

    assert storage.read(stored)
    assert memory_s3.last_body is not None
    assert memory_s3.last_body.closed


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


def test_default_factory_uses_configured_object_storage_and_skill_bucket(
    monkeypatch, memory_s3, package
):
    client_calls = []

    def create_client(service_name, **kwargs):
        client_calls.append((service_name, kwargs))
        return memory_s3

    monkeypatch.setattr("boto3.client", create_client)
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ENDPOINT", "https://objects.example.test")
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ACCESS_KEY", "configured-access")
    monkeypatch.setenv("IAP_OBJECT_STORAGE_SECRET_KEY", "configured-secret")
    monkeypatch.setenv("IAP_OBJECT_STORAGE_REGION", "cn-test-1")
    monkeypatch.setenv("IAP_SKILL_BUCKET", "configured-skills")

    storage = create_default_skill_package_storage()
    stored = storage.put("unit", "project", "skill", package)

    assert client_calls == [
        (
            "s3",
            {
                "endpoint_url": "https://objects.example.test",
                "aws_access_key_id": "configured-access",
                "aws_secret_access_key": "configured-secret",
                "region_name": "cn-test-1",
                "config": STORAGE_CLIENT_CONFIG,
            },
        )
    ]
    assert ("configured-skills", stored.object_key) in memory_s3.objects


def test_default_factory_uses_object_storage_defaults(monkeypatch, memory_s3):
    client_calls = []

    def create_client(service_name, **kwargs):
        client_calls.append((service_name, kwargs))
        return memory_s3

    monkeypatch.setattr("boto3.client", create_client)
    for name in (
        "IAP_OBJECT_STORAGE_ENDPOINT",
        "IAP_OBJECT_STORAGE_ACCESS_KEY",
        "IAP_OBJECT_STORAGE_SECRET_KEY",
        "IAP_OBJECT_STORAGE_REGION",
        "IAP_SKILL_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    create_default_skill_package_storage()

    _, kwargs = client_calls[0]
    assert kwargs == {
        "endpoint_url": "http://minio:9000",
        "aws_access_key_id": "iap-access",
        "aws_secret_access_key": "change-me",
        "region_name": "us-east-1",
        "config": STORAGE_CLIENT_CONFIG,
    }


def test_shared_settings_are_frozen_redacted_and_supply_client_and_bucket(
    monkeypatch, memory_s3, package
):
    settings = package_storage.SkillStorageSettings(
        endpoint_url="https://shared.example.test",
        access_key_id="shared-access-sensitive",
        secret_access_key="shared-secret-sensitive",
        region_name="eu-west-1",
        bucket="shared-skill-bucket",
    )
    assert "shared-access-sensitive" not in repr(settings)
    assert "shared-secret-sensitive" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.bucket = "changed"
    monkeypatch.setattr(
        package_storage, "load_skill_storage_settings", lambda: settings
    )
    calls = []

    def create_client(service, **kwargs):
        calls.append((service, kwargs))
        return memory_s3

    monkeypatch.setattr("boto3.client", create_client)
    storage = create_default_skill_package_storage()
    stored = storage.put("unit", "project", "skill", package)

    assert calls == [
        (
            "s3",
            {
                "endpoint_url": "https://shared.example.test",
                "aws_access_key_id": "shared-access-sensitive",
                "aws_secret_access_key": "shared-secret-sensitive",
                "region_name": "eu-west-1",
                "config": STORAGE_CLIENT_CONFIG,
            },
        )
    ]
    assert ("shared-skill-bucket", stored.object_key) in memory_s3.objects


@pytest.mark.parametrize(
    "scope", ["", ".", "..", "has/slash", "has\\slash", " leading"]
)
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


def test_rejects_declared_length_mismatch_and_closes_without_reading(
    memory_s3, package
):
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
    memory_s3.objects[("test-skills", stored.object_key)]["Metadata"][
        "package-digest"
    ] = ("0" * 64)

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)


def test_stops_at_archive_limit_and_closes_stream(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = storage.put("unit", "project", "skill", package)
    body = TrackingBody(
        b"x" * (MAX_ZIP_BYTES + 1024), declared_length=stored.size_bytes
    )
    memory_s3.next_body = body

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert body.bytes_read <= MAX_ZIP_BYTES + 64 * 1024
    assert body.closed


def test_rejects_real_reencoded_archive_over_size_limit(memory_s3):
    repeated_block = random.Random(0).randbytes(1024 * 1024)
    bundle = make_bundle(
        [
            ("s/SKILL.md", valid_manifest()),
            ("s/repeated.bin", repeated_block * 11),
        ],
        compression=zipfile.ZIP_LZMA,
    )
    assert len(bundle) < MAX_ZIP_BYTES
    package = parse_skill_bundle(bundle)[0]
    storage = SkillPackageStorage(memory_s3, "test-skills")

    with pytest.raises(SkillPackageStorageError, match="Encoded skill package exceeds"):
        storage.put("unit", "project", "skill", package)

    assert memory_s3.objects == {}


def test_rejects_arbitrary_object_key_without_remote_read(memory_s3):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    stored = StoredSkillPackage("other-bucket-object", "0" * 64, "1" * 64, 1)

    with pytest.raises(SkillPackageStorageError):
        storage.read(stored)

    assert memory_s3.get_calls == 0
