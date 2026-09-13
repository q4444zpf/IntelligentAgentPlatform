import base64
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.runtime.execution_snapshot import SnapshotSkill, SnapshotSkillFile
from app.runtime.skill_resources import SkillResourceMaterializer
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
    try:
        (root / "forecast").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable on Windows: {error}")

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


def test_materializer_writes_embedded_files_under_skill_root(tmp_path):
    from app.runtime.execution_snapshot import ExecutionSnapshotPayload, PublishedAgentSnapshot, SnapshotModelSelection, SnapshotRuntimeLimits
    from datetime import datetime, UTC

    data = b"rules"
    skill = SnapshotSkill(name="forecast", files=(_file("references/rules.txt", data),))
    snapshot = ExecutionSnapshotPayload(
        snapshot_id="snap", run_id="run", unit_id="unit", project_id="project", user_id="user",
        actor=PublishedAgentSnapshot(id="a", name="a", description="", runtime_form="common", language="zh", system_prompt="", context_prompt="", approval_policy="never"),
        model=SnapshotModelSelection(provider_id="p", model="m"), messages=(), skills=(skill,),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=100000), created_at=datetime.now(UTC),
    )
    root = SkillResourceMaterializer().materialize(snapshot, object(), tmp_path)
    assert root == tmp_path / "skills" / "forecast"
    assert (root / "references" / "rules.txt").read_bytes() == data


def test_materializer_retry_replaces_existing_tree_atomically(tmp_path):
    from app.runtime.execution_snapshot import ExecutionSnapshotPayload, PublishedAgentSnapshot, SnapshotModelSelection, SnapshotRuntimeLimits
    from datetime import datetime, UTC

    data = b"rules"
    skill = SnapshotSkill(name="forecast", files=(_file("rules.txt", data),))
    snapshot = ExecutionSnapshotPayload(
        snapshot_id="snap", run_id="run", unit_id="unit", project_id="project", user_id="user",
        actor=PublishedAgentSnapshot(id="a", name="a", description="", runtime_form="common", language="zh", system_prompt="", context_prompt="", approval_policy="never"),
        model=SnapshotModelSelection(provider_id="p", model="m"), messages=(), skills=(skill,),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=100000), created_at=datetime.now(UTC),
    )
    materializer = SkillResourceMaterializer()
    materializer.materialize(snapshot, object(), tmp_path)
    materializer.materialize(snapshot, object(), tmp_path)
    assert (tmp_path / "skills" / "forecast" / "rules.txt").read_bytes() == data


def test_materializer_reads_client_backed_file_and_rolls_back_old_tree(tmp_path):
    from app.runtime.execution_snapshot import ExecutionSnapshotPayload, PublishedAgentSnapshot, SnapshotModelSelection, SnapshotRuntimeLimits
    from datetime import datetime, UTC

    first = b"new"
    second = b"other"
    skill = SnapshotSkill(
        name="forecast",
        object_key="unit/project/skill/package.zip",
        package_digest="a" * 64,
        archive_sha256="b" * 64,
        size_bytes=10,
        files=(
            SnapshotSkillFile(path="a.txt", size=len(first), sha256=hashlib.sha256(first).hexdigest()),
            SnapshotSkillFile(path="b.txt", size=len(second), sha256=hashlib.sha256(second).hexdigest()),
        ),
    )
    snapshot = ExecutionSnapshotPayload(
        snapshot_id="snap", run_id="run", unit_id="unit", project_id="project", user_id="user",
        actor=PublishedAgentSnapshot(id="a", name="a", description="", runtime_form="common", language="zh", system_prompt="", context_prompt="", approval_policy="never"),
        model=SnapshotModelSelection(provider_id="p", model="m"), messages=(), skills=(skill,),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=100000), created_at=datetime.now(UTC),
    )
    old = tmp_path / "skills" / "forecast"
    old.mkdir(parents=True)
    (old / "old.txt").write_bytes(b"old")

    class Client:
        def read_skill_file(self, _name, path):
            if path == "b.txt":
                raise RuntimeError("unavailable")
            return {"skill_name": "forecast", "path": path, "size": len(first), "sha256": hashlib.sha256(first).hexdigest(), "data": first}

    with pytest.raises(Exception):
        SkillResourceMaterializer().materialize(snapshot, Client(), tmp_path)
    assert (old / "old.txt").read_bytes() == b"old"


def test_materializer_rejects_traversal_manifest_without_writing(tmp_path):
    from app.runtime.execution_snapshot import ExecutionSnapshotPayload, PublishedAgentSnapshot, SnapshotModelSelection, SnapshotRuntimeLimits
    from datetime import datetime, UTC
    unsafe = SnapshotSkill.model_construct(name="forecast", enabled=True, files=(SnapshotSkillFile.model_construct(path="../escape", size=1, sha256="a" * 64, content_base64=base64.b64encode(b"x").decode()),))
    snapshot = ExecutionSnapshotPayload(snapshot_id="s", run_id="r", unit_id="u", project_id="p", user_id="u", actor=PublishedAgentSnapshot(id="a", name="a", description="", runtime_form="common", language="zh", system_prompt="", context_prompt="", approval_policy="never"), model=SnapshotModelSelection(provider_id="p", model="m"), messages=(), skills=(unsafe,), limits=__import__("app.runtime.execution_snapshot", fromlist=["SnapshotRuntimeLimits"]).SnapshotRuntimeLimits(snapshot_max_bytes=100), created_at=datetime.now(UTC))
    with pytest.raises(Exception):
        SkillResourceMaterializer().materialize(snapshot, object(), tmp_path)
    assert not (tmp_path / "skills").exists()
