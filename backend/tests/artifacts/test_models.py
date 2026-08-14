import hashlib

import pytest
from app.artifacts.schemas import ArtifactCreateRequest
from pydantic import ValidationError


def test_artifact_request_requires_scope_and_positive_size():
    request = ArtifactCreateRequest(
        filename="report.txt",
        content_type="text/plain",
        size_bytes=5,
        sha256=hashlib.sha256(b"hello").hexdigest(),
        scope="project",
    )

    assert request.scope == "project"
    assert request.size_bytes == 5

    with pytest.raises(ValidationError):
        ArtifactCreateRequest(
            filename="report.txt",
            content_type="text/plain",
            size_bytes=0,
            sha256="bad",
            scope="project",
        )
