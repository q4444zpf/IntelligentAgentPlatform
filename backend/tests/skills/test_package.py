from __future__ import annotations

import io
import stat
import struct
import zipfile
from dataclasses import FrozenInstanceError

import pytest

from app.skills.package import SkillPackageError, parse_skill_bundle

MIB = 1024 * 1024


def valid_manifest(
    *, name: str = "s", description: str = "Sample", version: str = "1.0"
) -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\nversion: '{version}'\n"
        "---\nInstructions\n"
    ).encode()


def make_bundle(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(path, data)
    return stream.getvalue()


def make_bundle_with_invalid_utf8_member_name() -> bytes:
    bundle = bytearray(
        make_bundle([("s/SKILL.md", valid_manifest()), ("s/bad.txt", b"invalid name")])
    )
    valid_name = b"s/bad.txt"
    invalid_name = b"s/\xffad.txt"
    matches = 0
    for signature, flags_offset, name_length_offset, name_offset in (
        (b"PK\x03\x04", 6, 26, 30),
        (b"PK\x01\x02", 8, 28, 46),
    ):
        cursor = 0
        while (header := bundle.find(signature, cursor)) >= 0:
            name_length = struct.unpack_from("<H", bundle, header + name_length_offset)[
                0
            ]
            member_name_offset = header + name_offset
            member_name = bytes(
                bundle[member_name_offset : member_name_offset + name_length]
            )
            if member_name == valid_name:
                flags = struct.unpack_from("<H", bundle, header + flags_offset)[0]
                struct.pack_into("<H", bundle, header + flags_offset, flags | 0x800)
                bundle[member_name_offset : member_name_offset + name_length] = (
                    invalid_name
                )
                matches += 1
            cursor = member_name_offset + name_length
    assert matches == 2
    return bytes(bundle)


def test_parses_an_immutable_skill_package():
    package = parse_skill_bundle(
        make_bundle(
            [
                ("s/SKILL.md", valid_manifest()),
                ("s/references/readme.txt", b"reference"),
            ]
        )
    )[0]

    assert package.name == "s"
    assert package.description == "Sample"
    assert package.display_version == "1.0"
    assert package.content.endswith("Instructions\n")
    assert [item.path for item in package.files] == [
        "SKILL.md",
        "references/readme.txt",
    ]
    assert package.files[1].data == b"reference"
    assert (
        package.files[1].sha256
        == "52367a6622b19f08825e915fad80c542ad4f4c34dbcebad9f5007994b3e39208"
    )
    with pytest.raises(FrozenInstanceError):
        package.name = "changed"
    with pytest.raises(FrozenInstanceError):
        package.files[0].path = "changed"


@pytest.mark.parametrize("path", ["../x", "/x", "C:/x", "a/../../x", "a\\..\\x"])
def test_rejects_unsafe_member_paths(path):
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle([(path, b"x")]))


@pytest.mark.parametrize(
    "path",
    [
        "s/CON",
        "s/con.txt",
        "s/aux.md",
        "s/file. ",
        "s/file.",
        "s/a:b.txt",
        "s/a/./x.txt",
        "s/a//x.txt",
    ],
)
def test_rejects_platform_unsafe_member_paths(path):
    entries = [("s/SKILL.md", valid_manifest()), (path, b"x")]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


def test_rejects_nul_in_raw_member_path():
    bundle = bytearray(
        make_bundle([("s/SKILL.md", valid_manifest()), ("s/ab.txt", b"x")])
    )
    bundle[bundle.find(b"s/ab.txt") + 3] = 0
    central_name = bundle.find(b"s/ab.txt")
    bundle[central_name + 3] = 0
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(bytes(bundle))


def test_rejects_duplicate_normalized_paths():
    entries = [("s/SKILL.md", valid_manifest()), ("s/a.txt", b"a"), ("s/A.txt", b"b")]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


@pytest.mark.parametrize(
    "colliding_entries",
    [
        [("s/ref", b"file"), ("s/ref/a.txt", b"nested")],
        [("s/ref/a.txt", b"nested"), ("s/ref", b"file")],
        [("s/REF/a.txt", b"nested"), ("s/ref", b"file")],
    ],
)
def test_rejects_regular_file_directory_ancestor_collisions(colliding_entries):
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(
            make_bundle([("s/SKILL.md", valid_manifest()), *colliding_entries])
        )


def test_accepts_explicit_directory_with_descendant_file():
    package = parse_skill_bundle(
        make_bundle(
            [
                ("s/SKILL.md", valid_manifest()),
                ("s/references/", b""),
                ("s/references/readme.txt", b"reference"),
            ]
        )
    )[0]

    assert [item.path for item in package.files] == [
        "SKILL.md",
        "references/readme.txt",
    ]


def test_rejects_exact_duplicate_paths():
    entries = [("s/SKILL.md", valid_manifest()), ("s/a.txt", b"a"), ("s/a.txt", b"b")]
    with pytest.warns(UserWarning, match="Duplicate name"):
        bundle = make_bundle(entries)
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(bundle)


def test_digest_ignores_archive_member_order_and_metadata():
    entries = [("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"reference")]
    left = parse_skill_bundle(make_bundle(entries))[0]
    reversed_entries = list(reversed(entries))
    metadata_entries = []
    for path, data in reversed_entries:
        info = zipfile.ZipInfo(path, date_time=(2001, 2, 3, 4, 5, 6))
        metadata_entries.append((info, data))
    right = parse_skill_bundle(make_bundle(metadata_entries))[0]

    assert left.digest == right.digest
    assert [item.path for item in left.files] == ["SKILL.md", "ref.txt"]


@pytest.mark.parametrize(
    "changed_entries",
    [
        [("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"changed")],
        [("s/SKILL.md", valid_manifest()), ("s/renamed.txt", b"reference")],
    ],
)
def test_digest_changes_with_file_content_or_path(changed_entries):
    original = parse_skill_bundle(
        make_bundle([("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"reference")])
    )[0]
    changed = parse_skill_bundle(make_bundle(changed_entries))[0]
    assert original.digest != changed.digest


def test_rejects_empty_bundle():
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle([]))


def test_rejects_bundle_without_manifest():
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle([("s/readme.md", b"missing manifest")]))


def test_rejects_invalid_utf8_manifest():
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle([("s/SKILL.md", b"\xff")]))


def test_rejects_invalid_utf8_member_name_as_package_error():
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle_with_invalid_utf8_member_name())


def test_rejects_corrupt_member_crc():
    bundle = bytearray(
        make_bundle([("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"reference")])
    )
    central = bundle.find(b"PK\x01\x02")
    while central >= 0:
        name_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", bundle, central + 28
        )
        name = bytes(bundle[central + 46 : central + 46 + name_length])
        if name == b"s/ref.txt":
            struct.pack_into("<I", bundle, central + 16, 0)
            break
        central = bundle.find(
            b"PK\x01\x02", central + 46 + name_length + extra_length + comment_length
        )
    assert central >= 0

    with pytest.raises(SkillPackageError):
        parse_skill_bundle(bytes(bundle))


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO])
def test_rejects_links_and_special_files(file_type):
    unsafe = zipfile.ZipInfo("s/unsafe")
    unsafe.create_system = 3
    unsafe.external_attr = (file_type | 0o644) << 16
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(
            make_bundle([("s/SKILL.md", valid_manifest()), (unsafe, b"target")])
        )


def test_rejects_nested_skill_roots():
    entries = [
        ("s/SKILL.md", valid_manifest()),
        ("s/nested/SKILL.md", valid_manifest(name="nested")),
    ]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


def test_rejects_orphan_files():
    entries = [("s/SKILL.md", valid_manifest()), ("orphan.txt", b"orphan")]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


def test_rejects_duplicate_skill_names():
    entries = [
        ("first/SKILL.md", valid_manifest(name="same")),
        ("second/SKILL.md", valid_manifest(name="same")),
    ]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


def test_returns_multiple_skills_in_name_order():
    entries = [
        ("z-root/SKILL.md", valid_manifest(name="z-skill")),
        ("a-root/SKILL.md", valid_manifest(name="a-skill")),
    ]
    packages = parse_skill_bundle(make_bundle(entries))
    assert [package.name for package in packages] == ["a-skill", "z-skill"]


def test_accepts_exact_archive_size_limit():
    bundle = make_bundle([("s/SKILL.md", valid_manifest())])
    package = parse_skill_bundle(b"\0" * (10 * MIB - len(bundle)) + bundle)[0]
    assert package.name == "s"


def test_rejects_archive_over_size_limit():
    bundle = make_bundle([("s/SKILL.md", valid_manifest())])
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(bundle + b"\0" * (10 * MIB + 1 - len(bundle)))


def test_accepts_exact_entry_count_limit():
    entries = [("s/SKILL.md", valid_manifest())]
    entries.extend((f"s/files/{index:03}.txt", b"") for index in range(499))
    package = parse_skill_bundle(make_bundle(entries))[0]
    assert len(package.files) == 500


def test_rejects_entry_count_over_limit():
    entries = [("s/SKILL.md", valid_manifest())]
    entries.extend((f"s/files/{index:03}.txt", b"") for index in range(500))
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))


def test_accepts_exact_expanded_size_limit():
    manifest = valid_manifest()
    padding = b"x" * (20 * MIB - len(manifest))
    package = parse_skill_bundle(
        make_bundle([("s/SKILL.md", manifest), ("s/padding.bin", padding)])
    )[0]
    assert sum(len(item.data) for item in package.files) == 20 * MIB


def test_rejects_declared_expanded_size_over_limit():
    manifest = valid_manifest()
    padding = b"x" * (20 * MIB + 1 - len(manifest))
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(
            make_bundle([("s/SKILL.md", manifest), ("s/padding.bin", padding)])
        )


def test_rejects_actual_expanded_size_over_limit(monkeypatch):
    bundle = make_bundle(
        [("s/SKILL.md", valid_manifest()), ("s/padding.bin", b"x" * (20 * MIB))]
    )
    original = zipfile.ZipFile.infolist

    def underreported_sizes(archive):
        infos = original(archive)
        for info in infos:
            info.file_size = 0
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", underreported_sizes)
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(bundle)
