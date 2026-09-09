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


def test_skill_storage_initialization_is_explicit_and_api_independent():
    services = _compose()["services"]
    operation = services["skill-storage-init"]
    assert operation["profiles"] == ["operations"]
    assert operation["image"] == services["api"]["image"]
    assert operation["command"] == ["python", "-m", "app.skills.storage_bootstrap"]
    assert operation["restart"] == "no"
    assert operation["depends_on"] == {"minio": {"condition": "service_healthy"}}
    assert operation["environment"] == {
        "IAP_OBJECT_STORAGE_ENDPOINT": "${IAP_OBJECT_STORAGE_ENDPOINT:-http://minio:9000}",
        "IAP_OBJECT_STORAGE_ACCESS_KEY": "${IAP_OBJECT_STORAGE_ACCESS_KEY:-iap-access}",
        "IAP_OBJECT_STORAGE_SECRET_KEY": "${IAP_OBJECT_STORAGE_SECRET_KEY:-change-me-minio-secret}",
        "IAP_OBJECT_STORAGE_REGION": "${IAP_OBJECT_STORAGE_REGION:-us-east-1}",
        "IAP_SKILL_BUCKET": "${IAP_SKILL_BUCKET:-iap-skills}",
    }
    assert "skill-storage-init" not in services["api"].get("depends_on", {})
    assert "migrate" not in services["api"].get("depends_on", {})


def test_api_and_operation_share_configurable_skill_bucket():
    services = _compose()["services"]
    assert (
        services["api"]["environment"]["IAP_SKILL_BUCKET"]
        == "${IAP_SKILL_BUCKET:-iap-skills}"
    )
    assert _example_environment()["IAP_SKILL_BUCKET"] == "iap-skills"


def test_api_and_operation_share_configurable_storage_region():
    services = _compose()["services"]
    for service in ("api", "skill-storage-init"):
        assert services[service]["environment"]["IAP_OBJECT_STORAGE_REGION"] == (
            "${IAP_OBJECT_STORAGE_REGION:-us-east-1}"
        )
