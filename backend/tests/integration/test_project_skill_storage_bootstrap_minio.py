import os
from uuid import uuid4

import boto3
import pytest
from app.skills.storage_bootstrap import main
from botocore.config import Config
from botocore.exceptions import ClientError


def test_default_bootstrap_initializes_a_new_bucket_twice(monkeypatch):
    names = ("TEST_S3_ENDPOINT", "TEST_S3_ACCESS_KEY", "TEST_S3_SECRET_KEY")
    configuration = {name: os.environ.get(name) for name in names}
    if not all(configuration.values()):
        pytest.skip("Dedicated MinIO test configuration is required")
    region = os.environ.get("TEST_S3_REGION", "us-east-1")
    client = boto3.client(
        "s3",
        endpoint_url=configuration[names[0]],
        aws_access_key_id=configuration[names[1]],
        aws_secret_access_key=configuration[names[2]],
        region_name=region,
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
    )
    bucket = f"iap-project-skill-activation-{uuid4().hex}"
    with pytest.raises(ClientError) as missing:
        client.head_bucket(Bucket=bucket)
    assert missing.value.response["Error"]["Code"] in {
        "404",
        "NoSuchBucket",
        "NotFound",
    }
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ENDPOINT", configuration[names[0]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ACCESS_KEY", configuration[names[1]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_SECRET_KEY", configuration[names[2]])
    monkeypatch.setenv("IAP_OBJECT_STORAGE_REGION", region)
    monkeypatch.setenv("IAP_SKILL_BUCKET", bucket)
    try:
        for _ in range(2):
            with pytest.raises(SystemExit) as finished:
                main()
            assert finished.value.code == 0
            assert (
                client.head_bucket(Bucket=bucket)["ResponseMetadata"]["HTTPStatusCode"]
                == 200
            )
    finally:
        try:
            # Only this UUID bucket is ours; S3 refuses to delete it if not empty.
            client.delete_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"NoSuchBucket", "404"}:
                raise
        finally:
            client.close()
