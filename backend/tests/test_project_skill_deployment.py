from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _example_environment() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            key, value = stripped.split("=", 1)
            entries[key] = value
    return entries


def test_migration_is_an_explicit_operations_profile_job():
    compose = _compose()
    migrate = compose["services"]["migrate"]

    assert migrate["profiles"] == ["operations"]
    assert migrate["restart"] == "no"
    assert migrate["image"] == compose["services"]["api"]["image"]
    assert migrate["command"] == [
        "sh",
        "-c",
        "python -m alembic upgrade head && exec python -m app.migrations.sqlite_to_postgres",
    ]
    assert "migrate" not in compose["services"]["api"].get("depends_on", {})


def test_services_receive_only_their_required_project_skill_deployment_environment():
    compose = _compose()
    services = compose["services"]

    assert services["api"]["environment"]["IAP_PROJECT_SKILLS_API_ENABLED"] == (
        "${IAP_PROJECT_SKILLS_API_ENABLED:-false}"
    )
    assert services["migrate"]["environment"] == {
        "DATABASE_URL": "${DATABASE_URL:-postgresql+psycopg://iap:iap@postgres:5432/iap}",
        "LEGACY_SQLITE_DATA_DIR": "${LEGACY_SQLITE_DATA_DIR:-/data}",
    }
    assert services["migrate"]["volumes"] == ["model-provider-data:/data"]


def test_project_skill_api_is_disabled_in_example_environment():
    assert _example_environment()["IAP_PROJECT_SKILLS_API_ENABLED"] == "false"
