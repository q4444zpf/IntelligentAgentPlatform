import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.tests.skills.project_support import (
    make_context,
    make_test_app,
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
