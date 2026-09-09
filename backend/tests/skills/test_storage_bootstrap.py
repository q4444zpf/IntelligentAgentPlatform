import runpy
import sys
import traceback

import pytest
from app.skills import storage_bootstrap
from botocore.exceptions import ClientError, EndpointConnectionError

MESSAGE = "unable to initialize project skill bucket"
SECRET = "test-bootstrap-secret"
ACCESS = "test-bootstrap-access"


def s3_error(code, operation="HeadBucket"):
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"{ACCESS} {SECRET}"},
            "ResponseMetadata": {"HTTPStatusCode": 404 if str(code) == "404" else 400},
        },
        operation,
    )


class FakeS3:
    def __init__(self, heads=(None,), create_error=None):
        self.heads = iter(heads)
        self.create_error = create_error
        self.calls = []

    def head_bucket(self, **request):
        self.calls.append(("head", request))
        error = next(self.heads)
        if error is not None:
            raise error
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def create_bucket(self, **request):
        self.calls.append(("create", request))
        if self.create_error is not None:
            raise self.create_error
        return {"Location": "/test-skills", "ResponseMetadata": {"HTTPStatusCode": 200}}


def assert_safe_error(error):
    assert str(error) == MESSAGE
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert SECRET not in rendered
    assert ACCESS not in rendered


def test_accessible_bucket_is_not_created():
    client = FakeS3()
    storage_bootstrap.ensure_skill_bucket(client, "test-skills", "us-east-1")
    assert client.calls == [("head", {"Bucket": "test-skills"})]


@pytest.mark.parametrize("missing_code", [404, "404", "NoSuchBucket", "NotFound"])
def test_missing_bucket_is_created_and_verified(missing_code):
    client = FakeS3(heads=(s3_error(missing_code), None))
    storage_bootstrap.ensure_skill_bucket(client, "test-skills", "us-east-1")
    assert client.calls == [
        ("head", {"Bucket": "test-skills"}),
        ("create", {"Bucket": "test-skills"}),
        ("head", {"Bucket": "test-skills"}),
    ]


def test_owned_creation_race_requires_successful_head():
    client = FakeS3(
        heads=(s3_error("404"), None),
        create_error=s3_error("BucketAlreadyOwnedByYou", "CreateBucket"),
    )
    storage_bootstrap.ensure_skill_bucket(client, "test-skills", "us-east-1")
    assert client.calls == [
        ("head", {"Bucket": "test-skills"}),
        ("create", {"Bucket": "test-skills"}),
        ("head", {"Bucket": "test-skills"}),
    ]


def test_non_default_region_is_sent_only_on_create():
    client = FakeS3(heads=(s3_error("NoSuchBucket"), None))
    storage_bootstrap.ensure_skill_bucket(client, "test-skills", "eu-west-1")
    assert client.calls == [
        ("head", {"Bucket": "test-skills"}),
        (
            "create",
            {
                "Bucket": "test-skills",
                "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
            },
        ),
        ("head", {"Bucket": "test-skills"}),
    ]


@pytest.mark.parametrize(
    "phase", ["initial-head", "create", "verification", "race-verification"]
)
@pytest.mark.parametrize(
    "failure", ["AccessDenied", "BucketAlreadyExists", "endpoint", "network"]
)
def test_unapproved_failures_have_no_secret_bearing_exception_chain(phase, failure):
    error = (
        EndpointConnectionError(endpoint_url=f"https://{ACCESS}:{SECRET}@invalid.test")
        if failure == "endpoint"
        else (
            OSError(f"{ACCESS} {SECRET}") if failure == "network" else s3_error(failure)
        )
    )
    heads = (error,) if phase == "initial-head" else (s3_error("404"), error)
    create_error = (
        error
        if phase == "create"
        else (
            s3_error("BucketAlreadyOwnedByYou", "CreateBucket")
            if phase == "race-verification"
            else None
        )
    )
    client = FakeS3(heads=heads, create_error=create_error)
    with pytest.raises(storage_bootstrap.SkillBucketBootstrapError) as caught:
        storage_bootstrap.ensure_skill_bucket(client, "test-skills", "us-east-1")
    assert_safe_error(caught.value)
    assert [name for name, _ in client.calls] == (
        ["head"]
        if phase == "initial-head"
        else ["head", "create"] if phase == "create" else ["head", "create", "head"]
    )


@pytest.mark.parametrize(
    "code", ["404", "NoSuchBucket", "NotFound", "BucketAlreadyOwnedByYou"]
)
def test_post_create_head_must_be_successful_even_for_approved_prior_codes(code):
    client = FakeS3(heads=(s3_error("404"), s3_error(code)))
    with pytest.raises(storage_bootstrap.SkillBucketBootstrapError) as caught:
        storage_bootstrap.ensure_skill_bucket(client, "test-skills", "us-east-1")
    assert_safe_error(caught.value)
    assert [name for name, _ in client.calls] == ["head", "create", "head"]


@pytest.mark.parametrize("failure_phase", [None, "head", "factory"])
def test_cli_exit_and_output_contract(monkeypatch, capsys, failure_phase):
    client = FakeS3(
        heads=(s3_error("AccessDenied"),) if failure_phase == "head" else (None,)
    )

    def create_client(*args, **kwargs):
        if failure_phase == "factory":
            raise ValueError(f"{ACCESS} {SECRET}")
        return client

    monkeypatch.setattr("boto3.client", create_client)
    monkeypatch.setenv("IAP_OBJECT_STORAGE_ACCESS_KEY", ACCESS)
    monkeypatch.setenv("IAP_OBJECT_STORAGE_SECRET_KEY", SECRET)
    monkeypatch.setenv("IAP_SKILL_BUCKET", "test-skills")
    monkeypatch.delitem(sys.modules, "app.skills.storage_bootstrap", raising=False)
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("app.skills.storage_bootstrap", run_name="__main__")
    assert caught.value.code == (0 if failure_phase is None else 1)
    if failure_phase is not None:
        assert_safe_error(caught.value.__context__)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ("" if failure_phase is None else MESSAGE + "\n")
    assert client.calls == (
        [] if failure_phase == "factory" else [("head", {"Bucket": "test-skills"})]
    )
