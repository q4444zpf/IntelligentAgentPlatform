import pytest
from app.audit.models import AuditEvent
from app.skills.models import Skill, SkillDraft
from app.skills.project_validation import (
    project_skill_validation_exception_handler,
    utf8_safe,
)
from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.tests.skills.project_support import make_context, make_test_app
from backend.tests.skills.project_support import (
    memory_s3_fixture as _memory_s3_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    sessions_fixture as _sessions_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    storage_fixture as _storage_fixture,  # noqa: F401
)


def test_project_skill_surrogate_validation_error_is_utf8_safe(
    sessions, storage, memory_s3
):
    app = make_test_app(
        sessions,
        lambda: storage,
        context=make_context("skill.manage"),
    )
    app.add_exception_handler(
        RequestValidationError,
        project_skill_validation_exception_handler,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/project-skills",
            content=b'{"content":"\\ud800"}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert "detail" in response.json()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Skill)) == 0
        assert session.scalar(select(func.count()).select_from(SkillDraft)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert memory_s3.objects == {}


def test_utf8_safe_recursively_sanitizes_validation_values():
    assert utf8_safe({"\ud800": [b"\xff", ("\udfff",)]}) == {"?": ["\ufffd", ["?"]]}


def _make_validation_app(*, with_project_handler: bool) -> FastAPI:
    app = FastAPI()

    @app.post("/api/items/{item_id}")
    def update_item(item_id: int, value: int = Body(embed=True)):
        return {"item_id": item_id, "value": value}

    @app.post("/api/project-skills-other/{item_id}")
    def update_other(item_id: int):
        return {"item_id": item_id}

    if with_project_handler:
        app.add_exception_handler(
            RequestValidationError,
            project_skill_validation_exception_handler,
        )
    return app


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/items/not-an-integer", {"value": 1}),
        ("/api/items/1", {"value": "not-an-integer"}),
        ("/api/project-skills-other/not-an-integer", None),
    ],
)
def test_non_project_validation_matches_fastapi_default(path, body):
    default_client = TestClient(_make_validation_app(with_project_handler=False))
    wrapped_client = TestClient(_make_validation_app(with_project_handler=True))

    default_response = default_client.post(path, json=body)
    wrapped_response = wrapped_client.post(path, json=body)

    assert wrapped_response.status_code == default_response.status_code
    assert wrapped_response.json() == default_response.json()
    assert (
        wrapped_response.headers["content-type"]
        == default_response.headers["content-type"]
    )


def test_surrogate_non_project_validation_matches_fastapi_default():
    raw_body = b'{"value":"\\ud800"}'
    headers = {"Content-Type": "application/json"}
    default_client = TestClient(
        _make_validation_app(with_project_handler=False),
        raise_server_exceptions=False,
    )
    wrapped_client = TestClient(
        _make_validation_app(with_project_handler=True),
        raise_server_exceptions=False,
    )

    default_response = default_client.post(
        "/api/items/1", content=raw_body, headers=headers
    )
    wrapped_response = wrapped_client.post(
        "/api/items/1", content=raw_body, headers=headers
    )

    assert wrapped_response.status_code == default_response.status_code
    assert wrapped_response.content == default_response.content
    assert {
        name: wrapped_response.headers[name]
        for name in ("content-type", "content-length")
    } == {
        name: default_response.headers[name]
        for name in ("content-type", "content-length")
    }
