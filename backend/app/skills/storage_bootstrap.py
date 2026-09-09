from __future__ import annotations

import sys
from enum import Enum, auto
from typing import Any

from botocore.exceptions import ClientError

from .package_storage import (
    SkillStorageSettings,
    create_skill_storage_client,
    load_skill_storage_settings,
)

BOOTSTRAP_FAILURE = "unable to initialize project skill bucket"
MISSING_BUCKET_CODES = {404, "404", "NoSuchBucket", "NotFound"}


class SkillBucketBootstrapError(RuntimeError):
    pass


class _CallStatus(Enum):
    SUCCESS = auto()
    MISSING = auto()
    OWNED = auto()
    FAILED = auto()


def _call(client: Any, operation: str, request: dict[str, Any]) -> _CallStatus:
    try:
        getattr(client, operation)(**request)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in MISSING_BUCKET_CODES:
            return _CallStatus.MISSING
        if code == "BucketAlreadyOwnedByYou":
            return _CallStatus.OWNED
        return _CallStatus.FAILED
    except Exception:  # noqa: BLE001 - sanitize every external client failure
        return _CallStatus.FAILED
    return _CallStatus.SUCCESS


def ensure_skill_bucket(client: Any, bucket: str, region: str) -> None:
    status = _call(client, "head_bucket", {"Bucket": bucket})
    if status is _CallStatus.SUCCESS:
        return
    if status is _CallStatus.MISSING:
        request: dict[str, Any] = {"Bucket": bucket}
        if region != "us-east-1":
            request["CreateBucketConfiguration"] = {"LocationConstraint": region}
        status = _call(client, "create_bucket", request)
        if status in {_CallStatus.SUCCESS, _CallStatus.OWNED}:
            status = _call(client, "head_bucket", {"Bucket": bucket})
            if status is _CallStatus.SUCCESS:
                return
    # Raise after client exception handling so neither chain link retains secrets.
    raise SkillBucketBootstrapError(BOOTSTRAP_FAILURE) from None


def _default_client() -> tuple[SkillStorageSettings, Any] | None:
    try:
        config = load_skill_storage_settings()
        return config, create_skill_storage_client(config)
    except Exception:  # noqa: BLE001 - client construction can expose credentials
        return None


def main() -> None:
    try:
        configured = _default_client()
        if configured is None:
            raise SkillBucketBootstrapError(BOOTSTRAP_FAILURE) from None
        config, client = configured
        ensure_skill_bucket(client, config.bucket, config.region_name)
    except SkillBucketBootstrapError:
        print(BOOTSTRAP_FAILURE, file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0)


if __name__ == "__main__":
    main()
