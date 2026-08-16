from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class S3ObjectStorage:
    """Small S3-compatible adapter used by MinIO and production object storage."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        bucket: str | None = None,
        max_url_expiry: int = 900,
        public_endpoint: str | None = None,
    ) -> None:
        self.bucket = bucket or os.getenv("IAP_ARTIFACT_BUCKET", "iap-artifacts")
        self.max_url_expiry = max(60, max_url_expiry)
        self.public_endpoint = public_endpoint or os.getenv("IAP_OBJECT_STORAGE_PUBLIC_ENDPOINT")
        self.client = client or self._build_client()
        self._bucket_ready = False

    @staticmethod
    def _build_client() -> Any:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=os.getenv("IAP_OBJECT_STORAGE_ENDPOINT", "http://minio:9000"),
            aws_access_key_id=os.getenv("IAP_OBJECT_STORAGE_ACCESS_KEY", "iap-access"),
            aws_secret_access_key=os.getenv("IAP_OBJECT_STORAGE_SECRET_KEY", "change-me"),
            region_name=os.getenv("IAP_OBJECT_STORAGE_REGION", "us-east-1"),
        )

    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )

    def get_bytes(self, object_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        return bytes(response["Body"].read())

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as error:
            response = getattr(error, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def delete_object(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    def presigned_get_url(
        self,
        object_key: str,
        expires_seconds: int = 900,
        *,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        expires = min(max(60, int(expires_seconds)), self.max_url_expiry)
        params = {"Bucket": self.bucket, "Key": object_key}
        if response_content_type:
            params["ResponseContentType"] = response_content_type
        if response_content_disposition:
            params["ResponseContentDisposition"] = response_content_disposition
        url = self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires,
        )
        if not self.public_endpoint:
            return url
        signed = urlsplit(url)
        public = urlsplit(self.public_endpoint)
        return urlunsplit((public.scheme, public.netloc, signed.path, signed.query, signed.fragment))
