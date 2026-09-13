from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .execution_snapshot import (
    ExecutionSnapshotPayload,
    MAX_SKILL_RESOURCE_FILES,
    MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES,
    MAX_SKILL_RESOURCE_TOTAL_BYTES,
)


class SkillResourceMaterializationError(ValueError):
    """Raised when a snapshot resource cannot be safely materialized."""


def _safe_skill_name(value: str) -> str:
    if not value or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise SkillResourceMaterializationError("unsafe Skill name")
    return value


def _safe_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.as_posix() != value
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or ".." in path.parts
        or "\\" in value
    ):
        raise SkillResourceMaterializationError("unsafe Skill resource path")
    return path


class SkillResourceMaterializer:
    def __init__(self) -> None:
        self.resource_index: tuple[dict[str, Any], ...] = ()

    @property
    def index(self) -> tuple[dict[str, Any], ...]:
        return self.resource_index

    def materialize(
        self,
        snapshot: ExecutionSnapshotPayload,
        client,
        workspace: Path,
    ) -> Path:
        workspace = Path(workspace)
        if workspace.exists() and (workspace.is_symlink() or not workspace.is_dir()):
            raise SkillResourceMaterializationError("invalid workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        skills_dir = workspace / "skills"
        stage = workspace / f".skills-tmp-{uuid.uuid4().hex}"
        stage.mkdir(mode=0o700)
        backup: Path | None = None
        swapped = False
        index: list[dict[str, Any]] = []
        try:
            enabled = sorted(
                (skill for skill in snapshot.skills if skill.enabled),
                key=lambda item: item.name.casefold(),
            )
            names = [_safe_skill_name(skill.name).casefold() for skill in enabled]
            if len(names) != len(set(names)):
                raise SkillResourceMaterializationError("duplicate Skill names")
            total_files = sum(len(skill.files) for skill in enabled)
            total_bytes = sum(item.size for skill in enabled for item in skill.files)
            if total_files > MAX_SKILL_RESOURCE_FILES:
                raise SkillResourceMaterializationError("Skill resource file count exceeds budget")
            if total_bytes > MAX_SKILL_RESOURCE_TOTAL_BYTES:
                raise SkillResourceMaterializationError("Skill resource total exceeds budget")
            for skill in enabled:
                name = _safe_skill_name(skill.name)
                skill_stage = stage / name
                skill_stage.mkdir()
                seen: set[str] = set()
                for manifest in sorted(skill.files, key=lambda item: item.path):
                    path = _safe_path(manifest.path)
                    folded = path.as_posix().casefold()
                    if folded in seen:
                        raise SkillResourceMaterializationError("duplicate Skill resource path")
                    seen.add(folded)
                    if manifest.size > MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES:
                        raise SkillResourceMaterializationError("Skill resource file exceeds budget")
                    if manifest.content_base64 is not None:
                        try:
                            data = base64.b64decode(manifest.content_base64, validate=True)
                        except (ValueError, binascii.Error) as error:
                            raise SkillResourceMaterializationError("invalid embedded Skill resource") from error
                    else:
                        if client is None:
                            raise SkillResourceMaterializationError("Skill resource client unavailable")
                        try:
                            response = client.read_skill_file(skill.name, manifest.path)
                            data = response.get("data") if isinstance(response, dict) else None
                        except Exception as error:  # noqa: BLE001
                            raise SkillResourceMaterializationError("Skill resource read failed") from error
                        if not isinstance(data, bytes):
                            raise SkillResourceMaterializationError("Skill resource response invalid")
                        if response.get("path") != manifest.path or response.get("skill_name") != skill.name:
                            raise SkillResourceMaterializationError("Skill resource response mismatch")
                        if response.get("size") != manifest.size or response.get("sha256") != manifest.sha256:
                            raise SkillResourceMaterializationError("Skill resource response digest mismatch")
                    if len(data) != manifest.size or hashlib.sha256(data).hexdigest() != manifest.sha256:
                        raise SkillResourceMaterializationError("Skill resource digest mismatch")
                    target = skill_stage.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stage_resolved = skill_stage.resolve()
                    if any(part.is_symlink() for part in [skill_stage, *target.parent.parents]) or not target.resolve(strict=False).is_relative_to(stage_resolved):
                        raise SkillResourceMaterializationError("symbolic link in Skill resource path")
                    with target.open("xb") as stream:
                        stream.write(data)
                    index.append({"skill_name": skill.name, "path": manifest.path, "size": manifest.size, "sha256": manifest.sha256})

            staged_skills = stage / "skills"
            staged_skills.mkdir()
            for skill in enabled:
                os.replace(stage / skill.name, staged_skills / _safe_skill_name(skill.name))
            if skills_dir.exists() or skills_dir.is_symlink():
                if skills_dir.is_symlink() or not skills_dir.is_dir():
                    raise SkillResourceMaterializationError("invalid skills destination")
                backup = workspace / f".skills-old-{uuid.uuid4().hex}"
                os.replace(skills_dir, backup)
            os.replace(staged_skills, skills_dir)
            swapped = True
            self.resource_index = tuple(index)
            result = skills_dir / _safe_skill_name(enabled[0].name) if len(enabled) == 1 else skills_dir
            return result
        except Exception:
            if swapped and (skills_dir.exists() or skills_dir.is_symlink()):
                shutil.rmtree(skills_dir, ignore_errors=True)
            if backup is not None and backup.exists():
                os.replace(backup, skills_dir)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            if backup is not None and backup.exists() and skills_dir.exists():
                shutil.rmtree(backup, ignore_errors=True)
