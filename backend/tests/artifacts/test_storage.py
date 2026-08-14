from app.artifacts.storage import S3ObjectStorage


class FakeS3:
    def __init__(self):
        self.put_calls = []
        self.url_calls = []
        self.objects = {}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, **kwargs):
        class Body:
            def __init__(self, value):
                self.value = value

            def read(self):
                return self.value

        return {"Body": Body(self.objects[kwargs["Key"]])}

    def head_bucket(self, **kwargs):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def generate_presigned_url(self, client_method, **kwargs):
        self.url_calls.append({"ClientMethod": client_method, **kwargs})
        return "http://minio:9000/artifacts/signed"


class MissingBucketS3(FakeS3):
    def __init__(self):
        super().__init__()
        self.created_buckets = []

    def head_bucket(self, **kwargs):
        error = RuntimeError("bucket missing")
        error.response = {"Error": {"Code": "404"}}
        raise error

    def create_bucket(self, **kwargs):
        self.created_buckets.append(kwargs)


def test_storage_uploads_bytes_and_caps_signed_url_expiry():
    client = FakeS3()
    storage = S3ObjectStorage(client=client, bucket="artifacts", max_url_expiry=900)

    storage.put_bytes("units/u1/projects/p1/a1/report.txt", b"hello", "text/plain")
    url = storage.presigned_get_url("units/u1/projects/p1/a1/report.txt", 3600)

    assert client.put_calls == [{
        "Bucket": "artifacts",
        "Key": "units/u1/projects/p1/a1/report.txt",
        "Body": b"hello",
        "ContentType": "text/plain",
    }]
    assert url == "http://minio:9000/artifacts/signed"
    assert client.url_calls[0]["ClientMethod"] == "get_object"
    assert client.url_calls[0]["ExpiresIn"] == 900


def test_storage_reads_uploaded_bytes():
    client = FakeS3()
    storage = S3ObjectStorage(client=client, bucket="artifacts")
    storage.put_bytes("result.txt", b"hello", "text/plain")

    assert storage.get_bytes("result.txt") == b"hello"


def test_storage_rewrites_presigned_url_to_public_endpoint():
    client = FakeS3()
    storage = S3ObjectStorage(client=client, bucket="artifacts", public_endpoint="http://127.0.0.1:9000")

    assert storage.presigned_get_url("result.txt") == "http://127.0.0.1:9000/artifacts/signed"


def test_storage_creates_missing_bucket_before_upload():
    client = MissingBucketS3()
    storage = S3ObjectStorage(client=client, bucket="artifacts")

    storage.put_bytes("result.txt", b"hello", "text/plain")

    assert client.created_buckets == [{"Bucket": "artifacts"}]
    assert client.put_calls[0]["Bucket"] == "artifacts"
