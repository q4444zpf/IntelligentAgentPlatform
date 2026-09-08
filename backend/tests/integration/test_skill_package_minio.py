from __future__ import annotations

import io
import os
import uuid
import zipfile

import boto3
import pytest
from botocore.config import Config

from app.skills.package import parse_skill_bundle
from app.skills.package_storage import create_default_skill_package_storage


def test_default_storage_round_trips_immutable_package_through_minio(monkeypatch):
    names = ("TEST_S3_ENDPOINT", "TEST_S3_ACCESS_KEY", "TEST_S3_SECRET_KEY")
    config = {name: os.environ.get(name) for name in names}
    if not all(config.values()):
        pytest.skip("MinIO integration configuration is unavailable")

    client = boto3.client(
        "s3",
        endpoint_url=config["TEST_S3_ENDPOINT"],
        aws_access_key_id=config["TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=config["TEST_S3_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
    )
    bucket = f"iap-skill-test-{uuid.uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    stored = None
    try:
        monkeypatch.setenv("IAP_OBJECT_STORAGE_ENDPOINT", config["TEST_S3_ENDPOINT"])
        monkeypatch.setenv(
            "IAP_OBJECT_STORAGE_ACCESS_KEY", config["TEST_S3_ACCESS_KEY"]
        )
        monkeypatch.setenv(
            "IAP_OBJECT_STORAGE_SECRET_KEY", config["TEST_S3_SECRET_KEY"]
        )
        monkeypatch.setenv("IAP_OBJECT_STORAGE_REGION", "us-east-1")
        monkeypatch.setenv("IAP_SKILL_BUCKET", bucket)
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "s/SKILL.md",
                b"---\nname: s\ndescription: MinIO integration\nversion: '1.0'\n---\nRun\n",
            )
            archive.writestr("s/reference.txt", b"water conservancy")
        package = parse_skill_bundle(source.getvalue())[0]
        storage = create_default_skill_package_storage()

        stored = storage.put("unit", "project", "skill", package)
        restored = parse_skill_bundle(storage.read(stored))[0]

        assert restored.digest == package.digest
        assert restored.files[1].data == b"water conservancy"
    finally:
        if stored is not None:
            client.delete_object(Bucket=bucket, Key=stored.object_key)
        client.delete_bucket(Bucket=bucket)
