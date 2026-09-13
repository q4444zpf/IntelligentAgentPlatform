import base64
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.runtime.execution_snapshot import SnapshotSkill, SnapshotSkillFile
from app.skills.service import SkillService, SkillValidationError


def _file(path="SKILL.md", data=b"hello"):
    return SnapshotSkillFile(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_base64=base64.b64encode(data).decode("ascii"),
    )


@pytest.mark.parametrize("path", ["", "../secret.txt", "/tmp/file", "C:/file.txt"])
def test_snapshot_skill_file_rejects_noncanonical_paths(path):
    with pytest.raises(ValidationError):
        _file(path)


def test_snapshot_skill_rejects_case_insensitive_duplicate_paths():
    with pytest.raises(ValidationError, match="duplicate"):
        SnapshotSkill(
            name="forecast",
            files=(_file("README.txt"), _file("readme.txt")),
        )


def test_snapshot_skill_file_rejects_mismatched_embedded_content():
    with pytest.raises(ValidationError, match="size|SHA-256|digest"):
        SnapshotSkillFile(
            path="SKILL.md",
            size=3,
            sha256=hashlib.sha256(b"no").hexdigest(),
            content_base64=base64.b64encode(b"yes").decode("ascii"),
        )


def test_snapshot_skill_file_requires_lowercase_sha256():
    with pytest.raises(ValidationError):
        SnapshotSkillFile(path="SKILL.md", size=0, sha256="A" * 64)


def test_snapshot_skill_enforces_file_count_single_file_and_total_budgets(monkeypatch):
    import app.runtime.execution_snapshot as snapshots

    monkeypatch.setattr(snapshots, "MAX_SKILL_RESOURCE_FILES", 1)
    with pytest.raises(ValidationError, match="file count"):
        SnapshotSkill(name="forecast", files=(_file("a"), _file("b")))

    monkeypatch.setattr(snapshots, "MAX_SKILL_RESOURCE_FILES", 10)
    monkeypatch.setattr(snapshots, "MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES", 2)
    with pytest.raises(ValidationError, match="file exceeds"):
        SnapshotSkill(name="forecast", files=(_file("a", b"123"),))

    monkeypatch.setattr(snapshots, "MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES", 10)
    monkeypatch.setattr(snapshots, "MAX_SKILL_RESOURCE_TOTAL_BYTES", 3)
    with pytest.raises(ValidationError, match="total exceeds"):
        SnapshotSkill(
            name="forecast",
            files=(_file("a", b"12"), _file("b", b"34")),
        )


def test_skill_service_read_files_returns_only_sorted_ordinary_files(tmp_path: Path):
    root = tmp_path / "skills"
    skill_root = root / "forecast"
    (skill_root / "references").mkdir(parents=True)
    (skill_root / "SKILL.md").write_bytes(b"manifest")
    (skill_root / "references" / "rules.txt").write_bytes(b"rules")
    (skill_root / ".skill-state.json").write_bytes(b"{}")

    files = SkillService(root).read_files("forecast")

    assert files == (("SKILL.md", b"manifest"), ("references/rules.txt", b"rules"))


def test_skill_service_rejects_skill_root_symlink(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_bytes(b"manifest")
    (root / "forecast").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillValidationError, match="symbolic link"):
        SkillService(root).read_files("forecast")


def test_object_backed_skill_resources_do_not_duplicate_embedded_bytes():
    skill = type(
        "Skill",
        (),
        {
            "name": "forecast",
            "description": "洪峰预测",
            "version": "1.2.0",
            "content": "manifest",
            "source": "project",
            "enabled": True,
            "tags": [],
            "metadata": {},
            "file_count": 1,
            "updated_at": None,
            "skill_id": "skill-1",
            "version_id": "version-1",
            "package_digest": "a" * 64,
            "object_key": "unit/project/skill/file.zip",
        },
    )()
    skill_service = type("SkillService", (), {})()
    skill_service.read_files = lambda name: (
        ("SKILL.md", b"manifest"),
    )
    skill_service.get = lambda name: skill
    service = type("Service", (), {})()
    service.agent_service = type("AgentService", (), {"skill_service": skill_service})()

    from app.runtime.execution_snapshot import ExecutionSnapshotService

    stored = ExecutionSnapshotService._snapshot_skill(service, "forecast", skill)

    assert stored.files[0].content_base64 is None
