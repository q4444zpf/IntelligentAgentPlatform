import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.tests.skills.project_support import (
    bundle,
    make_context,
    make_test_app,
    manifest,
    publish_skill,
    seed_skill,
)
from backend.tests.skills.project_support import (
    memory_s3_fixture as _memory_s3_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    sessions_fixture as _sessions_fixture,  # noqa: F401
)
from backend.tests.skills.project_support import (
    storage_fixture as _storage_fixture,  # noqa: F401
)


@pytest.mark.parametrize(
    "grant,status", [("none", 403), ("own", 404), ("other-project", 404)]
)
def test_write_permission_and_scope_precede_resource_access(
    sessions, storage, grant, status
):
    skill_id = seed_skill(
        sessions, storage, make_context("skill.manage", user_id="user-2")
    )
    context = (
        make_context("skill.read")
        if grant == "none"
        else make_context(
            "skill.manage",
            data_scope="own" if grant == "own" else "project",
            project_id="project-2" if grant == "other-project" else "project-1",
        )
    )
    with TestClient(
        make_test_app(sessions, lambda: storage, context=context)
    ) as client:
        response = client.put(
            f"/api/project-skills/{skill_id}/draft",
            json={"expected_revision": 1, "content": manifest("s", "Changed")},
        )
    assert response.status_code == status


def test_manage_grant_writes_without_requiring_read_grant(sessions, storage):
    context = make_context("skill.manage")
    with TestClient(
        make_test_app(sessions, lambda: storage, context=context)
    ) as client:
        created = client.post("/api/project-skills", json={"content": manifest("s")})
        assert created.status_code == 201
        changed = client.put(
            f"/api/project-skills/{created.json()['id']}/draft",
            json={"expected_revision": 1, "content": manifest("s", "Changed")},
        )
        assert changed.status_code == 200
        assert changed.headers["cache-control"] == "no-store"
        assert set(changed.json()) == {
            "skill_id",
            "name",
            "description",
            "display_version",
            "revision",
            "content",
            "files",
            "package_digest",
            "updated_at",
        }


def test_unknown_audit_failure_remains_generic_http500(sessions, storage):
    from app.skills.project_router import _service
    from app.skills.project_service import ProjectSkillService

    from backend.tests.skills.test_project_drafts import FailingAudit

    app = make_test_app(sessions, lambda: storage, context=make_context("skill.manage"))
    app.dependency_overrides[_service] = lambda: ProjectSkillService(
        sessions, storage_factory=lambda: storage, audit_recorder=FailingAudit()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/project-skills", json={"content": manifest("s")})
    assert response.status_code == 500
    assert "audit failed" not in response.text


def test_read_routes_return_explicit_payloads_and_no_store(sessions, storage):
    """Catches route wiring to wrong service methods or cacheable metadata responses."""
    context = make_context("skill.read")
    skill_id = seed_skill(
        sessions, storage, context, attachments=(("ref.txt", b"ref"),)
    )
    version_id = publish_skill(sessions, context, skill_id)
    client = TestClient(make_test_app(sessions, lambda: storage, context=context))

    expected_paths = [
        "/api/project-skills",
        f"/api/project-skills/{skill_id}",
        f"/api/project-skills/{skill_id}/draft",
        f"/api/project-skills/{skill_id}/versions",
        f"/api/project-skills/{skill_id}/versions/{version_id}",
    ]
    responses = [client.get(path) for path in expected_paths]

    assert all(response.status_code == 200 for response in responses)
    assert all(
        response.headers["cache-control"] == "no-store" for response in responses
    )
    assert responses[0].json()["items"][0]["id"] == skill_id
    assert "content" not in responses[0].json()["items"][0]
    assert responses[2].json()["skill_id"] == skill_id
    assert responses[4].json()["id"] == version_id


def test_list_query_model_rejects_unknown_scope_and_invalid_bounds(sessions, storage):
    """Catches silently ignored client ownership fields and loose page limits."""
    client = TestClient(
        make_test_app(sessions, lambda: storage, context=make_context("skill.read"))
    )
    for query in (
        "unit_id=unit-2",
        "project_id=project-2",
        "created_by=user-2",
        "offset=-1",
        "limit=0",
        "limit=101",
        f"q={'x' * 121}",
    ):
        assert client.get(f"/api/project-skills?{query}").status_code == 422
    assert client.get("/api/project-skills?offset=1&limit=2&q=ok").status_code == 200


def test_uuid_paths_reject_non_uuid_names_before_service_lookup(sessions, storage):
    """Catches ambiguous name routing into UUID resource reads."""
    client = TestClient(
        make_test_app(sessions, lambda: storage, context=make_context("skill.read"))
    )
    assert client.get("/api/project-skills/not-a-uuid").status_code == 422
    assert client.get(f"/api/project-skills/{uuid4()}").status_code == 404


def test_api_preserves_stable_401_403_404_codes(sessions, storage):
    """Catches HTTP translation that exposes exception text or merges failure classes."""
    allowed = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, allowed)
    cases = [
        (make_context(), skill_id, 403, "skill_permission_denied"),
        (allowed, str(uuid4()), 404, "skill_not_found"),
    ]
    for context, requested_id, status, detail in cases:
        client = TestClient(make_test_app(sessions, lambda: storage, context=context))
        response = client.get(f"/api/project-skills/{requested_id}")
        assert response.status_code == status
        assert response.json() == {"detail": detail}


def isolated_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}"
    environment["PYTHONPATH"] = str(Path(__file__).parents[2])
    for key in tuple(environment):
        if key.startswith("TEST_"):
            environment.pop(key)
    return environment


def test_project_router_import_constructs_neither_storage_nor_legacy_service(tmp_path):
    """Catches import-time initialization of MinIO or the legacy filesystem service."""
    script = """
import importlib
import app.skills.package_storage as package_storage
import app.skills.service as legacy_service

def forbidden(*args, **kwargs):
    raise AssertionError("constructor called during project_router import")

package_storage.create_default_skill_package_storage = forbidden
legacy_service.SkillService.__init__ = forbidden
router = importlib.import_module("app.skills.project_router")
assert router._service.__name__ == "_service"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=isolated_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_main_does_not_mount_project_router_and_preserves_legacy_routes(tmp_path):
    """Catches accidental app.main mutation or replacement of the old Skill API."""
    script = """
import app.skills.service as legacy_service
legacy_service.SkillService.__init__ = lambda self, *args, **kwargs: None
from app.main import app

def resolved_routes(route_items, prefix=""):
    for route in route_items:
        if hasattr(route, "methods"):
            for method in route.methods or ():
                yield method, prefix + route.path
        elif hasattr(route, "original_router"):
            nested_prefix = route.include_context.prefix
            yield from resolved_routes(
                route.original_router.routes, prefix + nested_prefix
            )

routes = set(resolved_routes(app.routes))
assert not any(path.startswith("/api/project-skills") for _, path in routes)
assert ("GET", "/api/skills") in routes
assert ("POST", "/api/skills") in routes
assert ("GET", "/api/skills/{skill_name}") in routes
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=isolated_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_import_route_accepts_zip_bytes_and_returns_only_result_metadata(
    sessions, storage
):
    data = bundle([("SKILL.md", manifest("imported").encode())])
    with TestClient(
        make_test_app(sessions, lambda: storage, context=make_context("skill.manage"))
    ) as client:
        response = client.post(
            "/api/project-skills/import", files={"file": ("bundle.bin", data)}
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {
        "items",
        "created_count",
        "updated_count",
        "skipped_count",
    }
    assert set(response.json()["items"][0]) == {
        "source_name",
        "action",
        "skill_id",
        "name",
        "draft_revision",
    }
    assert response.json()["created_count"] == 1


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "duplicate-file",
        "duplicate-manifest",
        "missing-file",
        "file-as-text",
        "manifest-as-file",
        "invalid-action",
        "duplicate-source",
        "manifest-too-large",
        "upload-too-large",
        "denied",
        "invalid-zip",
    ],
)
def test_import_rejection_closes_every_parsed_upload(
    sessions, storage, monkeypatch, case
):
    from app.skills.package import MAX_ZIP_BYTES
    from starlette.datastructures import UploadFile

    uploads = []
    original_init = UploadFile.__init__

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        uploads.append(self)

    monkeypatch.setattr(UploadFile, "__init__", tracked_init)
    data = bundle([("SKILL.md", manifest("a").encode())])
    files = [("file", ("bundle.zip", data))]
    expected = 422
    context = make_context("skill.manage")
    if case == "unknown":
        files.append(("unexpected", (None, "forged")))
    elif case == "duplicate-file":
        files.append(("file", ("second.zip", data)))
    elif case == "duplicate-manifest":
        files.extend([("manifest", (None, "[]")), ("manifest", (None, "[]"))])
    elif case == "missing-file":
        files = [("manifest", (None, "[]"))]
    elif case == "file-as-text":
        files = [("file", (None, "text"))]
    elif case == "manifest-as-file":
        files.append(("manifest", ("manifest.json", b"[]")))
    elif case == "invalid-action":
        files.append(("manifest", (None, '[{"source_name":"a","action":"unknown"}]')))
    elif case == "duplicate-source":
        files.append(
            (
                "manifest",
                (None, json.dumps([{"source_name": "a", "action": "skip"}] * 2)),
            )
        )
    elif case == "manifest-too-large":
        files.append(("manifest", (None, " " * (256 * 1024 + 1))))
    elif case == "upload-too-large":
        files = [("file", ("large.zip", b"x" * (MAX_ZIP_BYTES + 1)))]
        expected = 413
    elif case == "invalid-zip":
        files = [("file", ("bad.zip", b"bad"))]
    elif case == "denied":
        context = make_context("skill.read")
        expected = 403
    with TestClient(
        make_test_app(sessions, lambda: storage, context=context)
    ) as client:
        response = client.post("/api/project-skills/import", files=files)
    assert response.status_code == expected
    assert all(upload.file.closed for upload in uploads)
    if case not in {"missing-file", "file-as-text"}:
        assert uploads


def test_import_manage_check_precedes_business_upload_and_parse(sessions, monkeypatch):
    import app.skills.project_router as router
    import app.skills.project_service as service

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized import performed business I/O")

    monkeypatch.setattr(router, "_read_upload", forbidden)
    monkeypatch.setattr(service, "parse_skill_bundle", forbidden)
    with TestClient(
        make_test_app(sessions, forbidden, context=make_context("skill.read"))
    ) as client:
        response = client.post(
            "/api/project-skills/import", files={"file": ("a.zip", b"bad")}
        )
    assert response.status_code == 403


def test_upload_reader_bounds_reads_and_closes_on_failure():
    from app.skills.package import MAX_ZIP_BYTES
    from app.skills.project_errors import ProjectSkillError
    from app.skills.project_router import _read_upload
    from fastapi import UploadFile

    class TrackedStream(io.BytesIO):
        total = 0

        def read(self, size=-1):
            assert 0 < size <= 65536
            chunk = super().read(size)
            self.total += len(chunk)
            return chunk

    stream = TrackedStream(b"x" * (MAX_ZIP_BYTES + 100))
    with pytest.raises(ProjectSkillError) as caught:
        _read_upload(UploadFile(stream))
    assert caught.value.status_code == 413
    assert stream.total == MAX_ZIP_BYTES + 1
    assert stream.closed
