from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any

from botocore.config import Config

from .package import MAX_ZIP_BYTES, READ_CHUNK_BYTES, ValidatedSkillPackage


DEFAULT_SKILL_BUCKET = "iap-skills"
SCOPE_SEGMENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
STORED_OBJECT_KEY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?/"
    r"[0-9a-f]{32}\.zip$"
)
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
STORAGE_CLIENT_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


@dataclass(frozen=True)
class StoredSkillPackage:
    object_key: str
    archive_sha256: str
    package_digest: str
    size_bytes: int


class SkillPackageStorageError(RuntimeError):
    pass


class SkillPackageStorage:
    def __init__(self, client: Any, bucket: str):
        if not bucket:
            raise SkillPackageStorageError("Skill package bucket is required")
        self._client = client
        self._bucket = bucket

    def put(
        self,
        unit_id: str,
        project_id: str,
        skill_id: str,
        package: ValidatedSkillPackage,
    ) -> StoredSkillPackage:
        scope = tuple(_validate_scope_segment(value) for value in (unit_id, project_id, skill_id))
        archive = _encode_package(package)
        if len(archive) > MAX_ZIP_BYTES:
            raise SkillPackageStorageError("Encoded skill package exceeds 10 MiB")
        archive_sha256 = hashlib.sha256(archive).hexdigest()
        object_key = "/".join((*scope, f"{uuid.uuid4().hex}.zip"))
        metadata = {
            "archive-sha256": archive_sha256,
            "package-digest": package.digest,
            "size-bytes": str(len(archive)),
        }
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=archive,
                ContentLength=len(archive),
                Metadata=metadata,
            )
        except Exception as error:
            raise SkillPackageStorageError("Unable to store skill package") from error
        return StoredSkillPackage(
            object_key=object_key,
            archive_sha256=archive_sha256,
            package_digest=package.digest,
            size_bytes=len(archive),
        )

    def read(self, stored: StoredSkillPackage) -> bytes:
        if (
            not STORED_OBJECT_KEY.fullmatch(stored.object_key)
            or not SHA256_HEX.fullmatch(stored.archive_sha256)
            or not SHA256_HEX.fullmatch(stored.package_digest)
            or not 0 <= stored.size_bytes <= MAX_ZIP_BYTES
        ):
            raise SkillPackageStorageError("Invalid stored skill package reference")
        body = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=stored.object_key)
            body = response["Body"]
            if response.get("ContentLength") != stored.size_bytes:
                raise SkillPackageStorageError("Stored skill package length does not match")
            expected_metadata = {
                "archive-sha256": stored.archive_sha256,
                "package-digest": stored.package_digest,
                "size-bytes": str(stored.size_bytes),
            }
            metadata = {str(key).lower(): str(value) for key, value in response.get("Metadata", {}).items()}
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                raise SkillPackageStorageError("Stored skill package metadata does not match")

            chunks: list[bytes] = []
            size = 0
            while chunk := body.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_ZIP_BYTES or size > stored.size_bytes:
                    raise SkillPackageStorageError("Stored skill package exceeds its size limit")
                chunks.append(chunk)
            archive = b"".join(chunks)
            if size != stored.size_bytes:
                raise SkillPackageStorageError("Stored skill package length does not match")
            if hashlib.sha256(archive).hexdigest() != stored.archive_sha256:
                raise SkillPackageStorageError("Stored skill package digest does not match")
            return archive
        except SkillPackageStorageError:
            raise
        except Exception as error:
            raise SkillPackageStorageError("Unable to read skill package") from error
        finally:
            if body is not None:
                body.close()


def create_default_skill_package_storage() -> SkillPackageStorage:
    import boto3

    bucket = os.environ.get("IAP_SKILL_BUCKET", DEFAULT_SKILL_BUCKET)
    return SkillPackageStorage(boto3.client("s3", config=STORAGE_CLIENT_CONFIG), bucket)


def _validate_scope_segment(value: str) -> str:
    if not isinstance(value, str) or not SCOPE_SEGMENT.fullmatch(value):
        raise SkillPackageStorageError("Invalid skill package scope segment")
    return value


def _encode_package(package: ValidatedSkillPackage) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for package_file in sorted(package.files, key=lambda item: item.path):
            info = zipfile.ZipInfo(package_file.path, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, package_file.data)
    return stream.getvalue()
