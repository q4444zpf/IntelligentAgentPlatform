from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runner_has_dedicated_non_api_dockerfile():
    dockerfile = ROOT / "Dockerfile.runner"
    text = dockerfile.read_text(encoding="utf-8")

    assert "USER nobody" in text
    assert "app.runtime.run_worker" in text
    assert "uvicorn" not in text
    assert "EXPOSE" not in text
    assert "alembic upgrade" not in text
    assert "sqlite_to_postgres" not in text
    assert "docker" not in text.lower()


def test_launcher_image_uses_root_for_docker_socket_access():
    dockerfile = ROOT / "Dockerfile.launcher"
    assert "USER root" in dockerfile.read_text(encoding="utf-8")
