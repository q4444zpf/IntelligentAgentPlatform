import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.conversations.router import default_run_dispatcher
from app.main import SessionFactory, app, default_mcp_health_scheduler, settings
from app.skills.project_startup import ProjectSkillStartupError

PROJECT_METHODS = {
    "/api/project-skills": {"get", "post"},
    "/api/project-skills/import": {"post"},
    "/api/project-skills/{skill_id}": {"get"},
    "/api/project-skills/{skill_id}/draft": {"get", "put"},
    "/api/project-skills/{skill_id}/publish": {"post"},
    "/api/project-skills/{skill_id}/versions": {"get"},
    "/api/project-skills/{skill_id}/versions/{version_id}": {"get"},
}


def _enabled_openapi_methods() -> dict[str, set[str]]:
    backend_directory = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["IAP_PROJECT_SKILLS_API_ENABLED"] = "true"
    environment["PYTHONPATH"] = str(backend_directory)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from app.main import app; "
                "print(json.dumps({path: sorted(methods) for path, methods in "
                "app.openapi()['paths'].items()}))"
            ),
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return {
        path: set(methods)
        for path, methods in json.loads(result.stdout).items()
    }


def test_application_mounts_audit_router():
    paths = set(app.openapi()["paths"])
    assert "/api/audit/events" in paths
    assert "/api/audit/events/{event_id}" in paths
    assert "/api/audit/events/{event_id}/related" in paths


def test_project_skill_routes_are_absent_by_default():
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/api/project-skills") for path in paths)
    assert "/api/skills" in paths


def test_project_skill_request_is_an_ordinary_404_by_default():
    response = TestClient(app).get("/api/project-skills")
    assert response.status_code == 404


def test_project_skill_routes_are_mounted_when_enabled_in_a_fresh_interpreter():
    paths = _enabled_openapi_methods()
    project_paths = {
        path: methods
        for path, methods in paths.items()
        if path.startswith("/api/project-skills")
    }
    assert project_paths == PROJECT_METHODS
    assert "/api/skills" in paths


def test_application_shutdown_closes_run_dispatcher(monkeypatch):
    calls: list[tuple[bool, bool]] = []

    def shutdown(*, wait: bool, cancel_futures: bool) -> None:
        calls.append((wait, cancel_futures))

    monkeypatch.setattr(default_run_dispatcher, "shutdown", shutdown)

    with TestClient(app):
        pass

    assert calls == [(False, True)]


def test_application_validates_startup_before_starting_scheduler(monkeypatch):
    calls = []

    def validate_project_skills(enabled, session_factory):
        assert enabled is settings.project_skills_api_enabled
        assert session_factory is SessionFactory
        calls.append("project")

    monkeypatch.setattr(
        "app.main.validate_runner_gateway_startup",
        lambda: calls.append("runner"),
    )
    monkeypatch.setattr(
        "app.main.validate_project_skills_startup",
        validate_project_skills,
    )
    monkeypatch.setattr(
        default_mcp_health_scheduler,
        "start",
        lambda: calls.append("scheduler"),
    )
    monkeypatch.setattr(default_mcp_health_scheduler, "cancel", lambda: calls.append("cancel"))

    async def wait_closed():
        calls.append("closed")

    monkeypatch.setattr(default_mcp_health_scheduler, "wait_closed", wait_closed)
    with TestClient(app):
        pass
    assert calls == ["runner", "project", "scheduler", "cancel", "closed"]


def test_project_skill_startup_failure_prevents_scheduler_start(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "app.main.validate_runner_gateway_startup",
        lambda: calls.append("runner"),
    )

    def reject_project_skills(enabled, session_factory):
        assert enabled is settings.project_skills_api_enabled
        assert session_factory is SessionFactory
        calls.append("project")
        raise ProjectSkillStartupError("project skill database revision mismatch")

    monkeypatch.setattr(
        "app.main.validate_project_skills_startup",
        reject_project_skills,
    )
    monkeypatch.setattr(
        default_mcp_health_scheduler,
        "start",
        lambda: calls.append("scheduler"),
    )

    with (
        pytest.raises(ProjectSkillStartupError, match="revision mismatch"),
        TestClient(app),
    ):
        pass

    assert calls == ["runner", "project"]
