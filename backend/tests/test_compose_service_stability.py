from pathlib import Path

import yaml


def test_minio_restarts_without_losing_healthcheck_or_persistent_storage():
    compose_path = Path(__file__).resolve().parents[2] / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    minio = compose["services"]["minio"]

    assert minio["restart"] == "unless-stopped"
    assert minio["healthcheck"]
    assert "minio-data:/data" in minio["volumes"]
