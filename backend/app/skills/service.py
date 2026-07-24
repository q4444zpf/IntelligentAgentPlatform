from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from .schemas import SkillCreateRequest, SkillImportResponse, SkillInfo, SkillUpdateRequest


STATE_FILE = ".skill-state.json"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_FILES = 500


class SkillNotFoundError(Exception):
    pass


class SkillConflictError(Exception):
    pass


class SkillValidationError(Exception):
    pass


def parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        raise SkillValidationError("SKILL.md must start with YAML frontmatter")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)(.*)$", content, re.DOTALL)
    if not match:
        raise SkillValidationError("SKILL.md frontmatter is not closed")
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as error:
        raise SkillValidationError(f"Invalid SKILL.md frontmatter: {error}") from error
    if not isinstance(frontmatter, dict):
        raise SkillValidationError("SKILL.md frontmatter must be an object")
    name = str(frontmatter.get("name", ""))
    description = str(frontmatter.get("description", ""))
    if not NAME_PATTERN.fullmatch(name):
        raise SkillValidationError("SKILL.md frontmatter contains an invalid name")
    if not description.strip():
        raise SkillValidationError("SKILL.md frontmatter requires description")
    return frontmatter, match.group(2)


def update_manifest(content: str, *, name: str | None = None, description: str | None = None) -> str:
    frontmatter, body = parse_skill_markdown(content)
    if name is not None:
        frontmatter["name"] = name
    if description is not None:
        frontmatter["description"] = description
    header = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{header}\n---\n{body}"


class SkillService:
    def __init__(self, root: str | Path | None = None):
        default_root = Path(__file__).resolve().parents[2] / "data" / "skills"
        self.root = Path(root or default_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, name: str) -> Path:
        if not NAME_PATTERN.fullmatch(name):
            raise SkillValidationError("Invalid skill name")
        return self.root / name

    @staticmethod
    def _read_state(directory: Path) -> dict[str, Any]:
        path = directory / STATE_FILE
        if not path.exists():
            return {"enabled": True, "tags": [], "source": "imported"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SkillValidationError(f"Invalid state for skill '{directory.name}'") from error

    @staticmethod
    def _write_state(directory: Path, *, enabled: bool, tags: list[str], source: str) -> None:
        (directory / STATE_FILE).write_text(
            json.dumps({"enabled": enabled, "tags": tags, "source": source}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _info(self, directory: Path) -> SkillInfo:
        manifest = directory / "SKILL.md"
        if not manifest.is_file():
            raise SkillValidationError(f"Skill '{directory.name}' is missing SKILL.md")
        content = manifest.read_text(encoding="utf-8")
        frontmatter, _ = parse_skill_markdown(content)
        state = self._read_state(directory)
        files = [path for path in directory.rglob("*") if path.is_file() and path.name != STATE_FILE]
        updated = max((path.stat().st_mtime for path in files), default=directory.stat().st_mtime)
        metadata = frontmatter.get("metadata", {})
        return SkillInfo(
            name=str(frontmatter["name"]),
            description=str(frontmatter["description"]),
            version=str(frontmatter.get("version", frontmatter.get("version_text", ""))),
            content=content,
            source=state.get("source", "imported"),
            enabled=bool(state.get("enabled", True)),
            tags=list(state.get("tags", [])),
            metadata=metadata if isinstance(metadata, dict) else {},
            file_count=len(files),
            updated_at=datetime.fromtimestamp(updated, tz=UTC),
        )

    def list(self) -> list[SkillInfo]:
        skills = []
        for directory in sorted(self.root.iterdir(), key=lambda path: path.name):
            if directory.is_dir() and (directory / "SKILL.md").is_file():
                skills.append(self._info(directory))
        return skills

    def get(self, name: str) -> SkillInfo:
        directory = self._directory(name)
        if not directory.is_dir():
            raise SkillNotFoundError(name)
        return self._info(directory)

    @staticmethod
    def _validate_content(name: str, content: str) -> None:
        frontmatter, _ = parse_skill_markdown(content)
        if frontmatter["name"] != name:
            raise SkillValidationError("Skill name must match SKILL.md frontmatter name")

    def create(self, request: SkillCreateRequest) -> SkillInfo:
        directory = self._directory(request.name)
        if directory.exists():
            raise SkillConflictError(f"Skill '{request.name}' already exists")
        self._validate_content(request.name, request.content)
        content = update_manifest(request.content, description=request.description)
        directory.mkdir()
        try:
            (directory / "SKILL.md").write_text(content, encoding="utf-8")
            self._write_state(directory, enabled=request.enabled, tags=request.tags, source="created")
        except OSError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return self._info(directory)

    def update(self, name: str, request: SkillUpdateRequest) -> SkillInfo:
        directory = self._directory(name)
        if not directory.is_dir():
            raise SkillNotFoundError(name)
        self._validate_content(name, request.content)
        content = update_manifest(request.content, description=request.description)
        (directory / "SKILL.md").write_text(content, encoding="utf-8")
        source = self._read_state(directory).get("source", "created")
        self._write_state(directory, enabled=request.enabled, tags=request.tags, source=source)
        return self._info(directory)

    def toggle(self, name: str) -> SkillInfo:
        directory = self._directory(name)
        if not directory.is_dir():
            raise SkillNotFoundError(name)
        state = self._read_state(directory)
        self._write_state(
            directory,
            enabled=not bool(state.get("enabled", True)),
            tags=list(state.get("tags", [])),
            source=state.get("source", "imported"),
        )
        return self._info(directory)

    def delete(self, name: str) -> None:
        directory = self._directory(name)
        if not directory.is_dir():
            raise SkillNotFoundError(name)
        shutil.rmtree(directory)

    @staticmethod
    def _validate_archive(archive: zipfile.ZipFile) -> None:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise SkillValidationError(f"Skill archive exceeds {MAX_FILES} files")
        if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise SkillValidationError("Skill archive is too large after extraction")
        for item in entries:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            mode = item.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", item.filename):
                raise SkillValidationError(f"Unsafe path in skill archive: {item.filename}")
            if stat.S_ISLNK(mode):
                raise SkillValidationError(f"Unsafe symbolic link in skill archive: {item.filename}")

    def _available_name(self, requested: str) -> str:
        if not (self.root / requested).exists():
            return requested
        for suffix in range(2, 1000):
            candidate = f"{requested}-{suffix}"
            if len(candidate) <= 64 and not (self.root / candidate).exists():
                return candidate
        raise SkillConflictError(f"Unable to find an available name for '{requested}'")

    def import_zip(
        self,
        data: bytes,
        conflict_strategy: Literal["rename", "overwrite", "skip"] = "rename",
    ) -> SkillImportResponse:
        if len(data) > MAX_ZIP_BYTES:
            raise SkillValidationError("Skill archive exceeds 10 MB")
        imported: list[str] = []
        skipped: list[str] = []
        infos: list[SkillInfo] = []
        try:
            with tempfile.TemporaryDirectory(dir=self.root) as temp_name:
                temp_root = Path(temp_name)
                archive_path = temp_root / "bundle.zip"
                archive_path.write_bytes(data)
                with zipfile.ZipFile(archive_path) as archive:
                    self._validate_archive(archive)
                    for item in archive.infolist():
                        if item.is_dir():
                            continue
                        target = temp_root.joinpath(*PurePosixPath(item.filename.replace("\\", "/")).parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination)
                manifests = [path for path in temp_root.rglob("SKILL.md") if archive_path not in path.parents]
                if not manifests:
                    raise SkillValidationError("Skill archive does not contain SKILL.md")
                for manifest in sorted(manifests):
                    content = manifest.read_text(encoding="utf-8")
                    frontmatter, _ = parse_skill_markdown(content)
                    source_name = str(frontmatter["name"])
                    destination_name = source_name
                    destination = self.root / destination_name
                    if destination.exists():
                        if conflict_strategy == "skip":
                            skipped.append(source_name)
                            continue
                        if conflict_strategy == "rename":
                            destination_name = self._available_name(source_name)
                            destination = self.root / destination_name
                            content = update_manifest(content, name=destination_name)
                            manifest.write_text(content, encoding="utf-8")
                        else:
                            shutil.rmtree(destination)
                    source_directory = manifest.parent
                    shutil.move(str(source_directory), destination)
                    self._write_state(destination, enabled=True, tags=[], source="imported")
                    imported.append(destination_name)
                    infos.append(self._info(destination))
        except zipfile.BadZipFile as error:
            raise SkillValidationError("Uploaded file is not a valid ZIP archive") from error
        return SkillImportResponse(imported=imported, skipped=skipped, count=len(imported), skills=infos)
