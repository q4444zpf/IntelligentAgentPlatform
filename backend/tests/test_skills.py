import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.skills.router import create_router
from app.skills.service import SkillService


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(create_router(SkillService(tmp_path / "skills")), prefix="/api/skills")
    return TestClient(app)


def skill_content(name="flood-forecast", description="洪水预报数据处理", version="1.2.0"):
    return f'''---
name: {name}
description: "{description}"
version: "{version}"
metadata:
  author: 水利模型组
---
# 洪水预报

根据输入的站点与时间范围调用预报流程。
'''


def build_zip(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return stream.getvalue()


def test_creates_lists_updates_and_toggles_skill(client):
    created = client.post(
        "/api/skills",
        json={
            "name": "flood-forecast",
            "description": "洪水预报数据处理",
            "content": skill_content(),
            "tags": ["预报", "水文"],
            "enabled": True,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "flood-forecast"
    assert body["version"] == "1.2.0"
    assert body["file_count"] == 1
    assert body["metadata"]["author"] == "水利模型组"

    listed = client.get("/api/skills")
    assert listed.status_code == 200
    assert listed.json() == [body]

    updated = client.put(
        "/api/skills/flood-forecast",
        json={
            "description": "更新后的说明",
            "content": skill_content(description="旧的 frontmatter 说明", version="1.3.0"),
            "tags": ["预报"],
            "enabled": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == "1.3.0"
    assert updated.json()["description"] == "更新后的说明"
    assert 'description: 更新后的说明' in updated.json()["content"]

    toggled = client.patch("/api/skills/flood-forecast/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False


def test_validates_skill_name_and_frontmatter(client):
    invalid_name = client.post(
        "/api/skills",
        json={"name": "Bad Name", "description": "x", "content": skill_content(), "tags": [], "enabled": True},
    )
    assert invalid_name.status_code == 422

    mismatch = client.post(
        "/api/skills",
        json={"name": "river-routing", "description": "x", "content": skill_content(), "tags": [], "enabled": True},
    )
    assert mismatch.status_code == 422
    assert "frontmatter" in mismatch.text


def test_imports_skill_zip_with_assets_and_renames_conflicts(client):
    bundle = build_zip(
        {
            "flood-forecast/SKILL.md": skill_content(),
            "flood-forecast/scripts/run.py": "print('forecast')\n",
            "flood-forecast/references/schema.md": "# 参数说明\n",
        }
    )
    imported = client.post(
        "/api/skills/import?conflict_strategy=rename",
        files={"file": ("skills.zip", bundle, "application/zip")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == ["flood-forecast"]
    assert imported.json()["skills"][0]["file_count"] == 3

    second = client.post(
        "/api/skills/import?conflict_strategy=rename",
        files={"file": ("skills.zip", bundle, "application/zip")},
    )
    assert second.status_code == 200
    assert second.json()["imported"] == ["flood-forecast-2"]
    assert second.json()["skills"][0]["name"] == "flood-forecast-2"


def test_rejects_unsafe_or_invalid_zip(client):
    unsafe = build_zip({"../outside/SKILL.md": skill_content()})
    response = client.post(
        "/api/skills/import",
        files={"file": ("unsafe.zip", unsafe, "application/zip")},
    )
    assert response.status_code == 422
    assert "unsafe" in response.text.lower()

    missing_manifest = build_zip({"example/readme.md": "missing SKILL.md"})
    response = client.post(
        "/api/skills/import",
        files={"file": ("invalid.zip", missing_manifest, "application/zip")},
    )
    assert response.status_code == 422


def test_deletes_skill(client):
    client.post(
        "/api/skills",
        json={"name": "flood-forecast", "description": "x", "content": skill_content(), "tags": [], "enabled": True},
    )
    assert client.delete("/api/skills/flood-forecast").status_code == 200
    assert client.get("/api/skills/flood-forecast").status_code == 404
