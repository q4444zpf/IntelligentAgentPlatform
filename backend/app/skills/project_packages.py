import io
import zipfile
from dataclasses import dataclass

from .models import SkillDraft
from .package import SkillPackageError, ValidatedSkillPackage, parse_skill_bundle
from .package_storage import (
    SkillPackageStorage,
    SkillPackageStorageError,
    StoredSkillPackage,
)
from .project_errors import ProjectSkillError
from .service import update_manifest


@dataclass(frozen=True)
class DraftPackageSnapshot:
    skill_id: str
    revision: int
    name: str
    description: str
    display_version: str
    content: str
    files: tuple[tuple[str, int, str], ...]
    stored: StoredSkillPackage


def snapshot_draft(row: SkillDraft) -> DraftPackageSnapshot:
    try:
        files = tuple(
            (item["path"], item["size"], item["sha256"]) for item in row.files
        )
        if any(
            not isinstance(path, str)
            or type(size) is not int
            or not isinstance(digest, str)
            for path, size, digest in files
        ):
            raise ValueError("Invalid file manifest")
        return DraftPackageSnapshot(
            skill_id=row.skill_id,
            revision=row.revision,
            name=row.name,
            description=row.description,
            display_version=row.display_version,
            content=row.content,
            files=files,
            stored=StoredSkillPackage(
                object_key=row.object_key,
                archive_sha256=row.archive_sha256,
                package_digest=row.package_digest,
                size_bytes=row.size_bytes,
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectSkillError("skill_storage_unavailable", 503) from error


def read_verified_package(
    storage: SkillPackageStorage, snapshot: DraftPackageSnapshot
) -> ValidatedSkillPackage:
    try:
        packages = parse_skill_bundle(storage.read(snapshot.stored))
        if len(packages) != 1:
            raise SkillPackageError("A single skill package is required")
        package = packages[0]
        if (
            package.name != snapshot.name
            or package.description != snapshot.description
            or package.display_version != snapshot.display_version
            or package.content != snapshot.content
            or package.digest != snapshot.stored.package_digest
            or tuple((item.path, len(item.data), item.sha256) for item in package.files)
            != snapshot.files
        ):
            raise SkillPackageError("Stored package does not match its snapshot")
        return package
    except (SkillPackageError, SkillPackageStorageError) as error:
        raise ProjectSkillError("skill_storage_unavailable", 503) from error


def _parse_files(entries: list[tuple[str, bytes]]) -> ValidatedSkillPackage:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(path, data)
    packages = parse_skill_bundle(stream.getvalue())
    if len(packages) != 1:
        raise SkillPackageError("A single skill package is required")
    return packages[0]


def package_from_content(content: str) -> ValidatedSkillPackage:
    return _parse_files([("SKILL.md", content.encode("utf-8"))])


def replace_manifest(
    package: ValidatedSkillPackage, content: str
) -> ValidatedSkillPackage:
    return _parse_files(
        [
            (
                item.path,
                content.encode("utf-8") if item.path == "SKILL.md" else item.data,
            )
            for item in package.files
        ]
    )


def rename_manifest(
    package: ValidatedSkillPackage, target_name: str
) -> ValidatedSkillPackage:
    return replace_manifest(package, update_manifest(package.content, name=target_name))
