from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import zipfile
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import PurePosixPath

from .service import SkillValidationError, parse_skill_markdown

MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 500
READ_CHUNK_BYTES = 64 * 1024
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class SkillPackageFile:
    path: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class ValidatedSkillPackage:
    name: str
    description: str
    display_version: str
    content: str
    files: tuple[SkillPackageFile, ...]
    digest: str


class SkillPackageError(ValueError):
    pass


def parse_skill_bundle(data: bytes) -> tuple[ValidatedSkillPackage, ...]:
    if len(data) > MAX_ZIP_BYTES:
        raise SkillPackageError("Skill bundle exceeds 10 MiB")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            _reject_raw_nul_names(archive, len(entries))
            if not entries:
                raise SkillPackageError("Skill bundle is empty")
            if len(entries) > MAX_ENTRIES:
                raise SkillPackageError(f"Skill bundle exceeds {MAX_ENTRIES} entries")
            if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise SkillPackageError("Skill bundle exceeds 20 MiB expanded")

            normalized_entries = _validate_entries(entries)
            files = _read_files(archive, normalized_entries)
    except (UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise SkillPackageError("Invalid ZIP skill bundle") from error

    return _build_packages(files)


def _reject_raw_nul_names(archive: zipfile.ZipFile, entry_count: int) -> None:
    if archive.fp is None:
        raise SkillPackageError("Closed ZIP skill bundle")
    archive.fp.seek(archive.start_dir)
    for _ in range(entry_count):
        header = archive.fp.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise SkillPackageError("Invalid ZIP central directory")
        name_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", header, 28
        )
        raw_name = archive.fp.read(name_length)
        if b"\x00" in raw_name:
            raise SkillPackageError("NUL is not allowed in skill bundle paths")
        archive.fp.seek(extra_length + comment_length, io.SEEK_CUR)


def _validate_entries(
    entries: list[zipfile.ZipInfo],
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    normalized_entries: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    regular_files: set[str] = set()
    for item in entries:
        normalized = _normalize_path(item.filename)
        folded = normalized.as_posix().casefold()
        if folded in seen:
            raise SkillPackageError(f"Duplicate path in skill bundle: {item.filename}")
        seen.add(folded)
        _validate_member_type(item)
        if not item.is_dir():
            regular_files.add(folded)
        normalized_entries.append((item, normalized))

    sorted_paths = sorted(seen)
    for regular_file in regular_files:
        descendant_prefix = f"{regular_file}/"
        descendant_index = bisect_left(sorted_paths, descendant_prefix)
        if descendant_index < len(sorted_paths) and sorted_paths[
            descendant_index
        ].startswith(descendant_prefix):
            raise SkillPackageError(
                f"Regular file is a directory ancestor in skill bundle: {regular_file}"
            )
    return normalized_entries


def _normalize_path(raw_path: str) -> PurePosixPath:
    if "\x00" in raw_path:
        raise SkillPackageError("NUL is not allowed in skill bundle paths")
    if raw_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw_path):
        raise SkillPackageError(f"Absolute path in skill bundle: {raw_path}")

    replaced = raw_path.replace("\\", "/")
    raw_parts = (
        replaced[:-1].split("/") if replaced.endswith("/") else replaced.split("/")
    )
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SkillPackageError(f"Unsafe path in skill bundle: {raw_path}")
    path = PurePosixPath(replaced)
    if not path.parts:
        raise SkillPackageError(f"Unsafe path in skill bundle: {raw_path}")
    for part in path.parts:
        if ":" in part or part.endswith((".", " ")):
            raise SkillPackageError(f"Platform-unsafe path in skill bundle: {raw_path}")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise SkillPackageError(f"Reserved path in skill bundle: {raw_path}")
    return path


def _validate_member_type(item: zipfile.ZipInfo) -> None:
    mode = item.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if item.is_dir():
        if file_type not in {0, stat.S_IFDIR}:
            raise SkillPackageError(f"Invalid directory entry: {item.filename}")
        return
    if file_type not in {0, stat.S_IFREG}:
        raise SkillPackageError(f"Special file in skill bundle: {item.filename}")


def _read_files(
    archive: zipfile.ZipFile,
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath]],
) -> dict[PurePosixPath, bytes]:
    total = 0
    files: dict[PurePosixPath, bytes] = {}
    for item, path in entries:
        if item.is_dir():
            continue
        chunks: list[bytes] = []
        with archive.open(item) as source:
            while chunk := source.read(READ_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_EXPANDED_BYTES:
                    raise SkillPackageError("Skill bundle exceeds 20 MiB expanded")
                chunks.append(chunk)
        files[path] = b"".join(chunks)
    return files


def _build_packages(
    files: dict[PurePosixPath, bytes],
) -> tuple[ValidatedSkillPackage, ...]:
    manifest_paths = sorted(path for path in files if path.name == "SKILL.md")
    if not manifest_paths:
        raise SkillPackageError("Skill bundle does not contain SKILL.md")
    roots = [path.parent for path in manifest_paths]
    for root in roots:
        if any(other != root and _is_relative_to(root, other) for other in roots):
            raise SkillPackageError("Nested skill roots are not allowed")

    owned_files: dict[PurePosixPath, list[tuple[PurePosixPath, bytes]]] = {
        root: [] for root in roots
    }
    for path, file_data in files.items():
        owners = [root for root in roots if _is_relative_to(path, root)]
        if len(owners) != 1:
            raise SkillPackageError(
                f"File does not belong to exactly one skill: {path}"
            )
        root = owners[0]
        owned_files[root].append((path.relative_to(root), file_data))

    packages: list[ValidatedSkillPackage] = []
    names: set[str] = set()
    for root in roots:
        manifest_data = files[root / "SKILL.md"]
        try:
            content = manifest_data.decode("utf-8")
            frontmatter, _ = parse_skill_markdown(content)
        except (UnicodeDecodeError, SkillValidationError) as error:
            raise SkillPackageError(f"Invalid SKILL.md in {root}") from error
        name = str(frontmatter["name"])
        if name in names:
            raise SkillPackageError(f"Duplicate skill name: {name}")
        names.add(name)

        package_files = tuple(
            SkillPackageFile(
                path=path.as_posix(),
                data=file_data,
                sha256=hashlib.sha256(file_data).hexdigest(),
            )
            for path, file_data in sorted(
                owned_files[root], key=lambda entry: entry[0].as_posix()
            )
        )
        digest_entries = [
            {"path": item.path, "size": len(item.data), "sha256": item.sha256}
            for item in package_files
        ]
        digest_payload = json.dumps(
            digest_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        packages.append(
            ValidatedSkillPackage(
                name=name,
                description=str(frontmatter["description"]),
                display_version=str(
                    frontmatter.get("version", frontmatter.get("version_text", ""))
                ),
                content=content,
                files=package_files,
                digest=hashlib.sha256(digest_payload).hexdigest(),
            )
        )
    return tuple(sorted(packages, key=lambda package: package.name))


def _is_relative_to(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
