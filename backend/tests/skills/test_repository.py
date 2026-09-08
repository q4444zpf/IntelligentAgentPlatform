from dataclasses import replace
import io
from pathlib import Path
import subprocess
import sys
import zipfile
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.skills.package import parse_skill_bundle
from app.skills.package_storage import SkillPackageStorage


class PackageSink:
    def put_object(self, **request):
        pass


def package_inputs(scope, skill_id, body="Initial instructions"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: reservoir-review\ndescription: Review reservoir\nversion: '1.0'\n---\n" + body,
        )
        archive.writestr("references/rules.txt", "Check inflow")
    package = parse_skill_bundle(stream.getvalue())[0]
    stored = SkillPackageStorage(PackageSink(), "test-skills").put(
        scope.unit_id, scope.project_id, skill_id, package
    )
    return package, stored


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'skills.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def scope():
    from app.skills.repository import SkillScope

    return SkillScope(str(uuid4()), str(uuid4()))


def create_skill(session, scope, *, name="reservoir-review", skill_id=None):
    from app.skills.repository import SkillRepository

    skill_id = skill_id or str(uuid4())
    package, stored = package_inputs(scope, skill_id)
    return SkillRepository(session).create(
        scope, skill_id=skill_id, name=name, created_by="editor", package=package, stored=stored
    )


def test_registers_skill_tables(engine):
    assert {"skills", "skill_drafts", "skill_versions"} <= set(inspect(engine).get_table_names())


def test_names_are_unique_per_unit_and_project(engine, scope):
    from app.skills.repository import SkillRepository, SkillScope

    other = SkillScope(scope.unit_id, str(uuid4()))
    other_unit = SkillScope(str(uuid4()), scope.project_id)
    with Session(engine) as session:
        created = [create_skill(session, item).id for item in (scope, other, other_unit)]
        session.commit()
    with Session(engine) as session:
        assert [item.id for item in SkillRepository(session).list(scope)] == created[:1]
        with pytest.raises(IntegrityError):
            create_skill(session, scope)


@pytest.mark.parametrize("different", ["unit_id", "project_id"])
def test_hides_cross_scope_resources_for_every_operation(engine, scope, different):
    from app.skills.repository import SkillRepository, SkillResourceNotFound

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        version_id = SkillRepository(session).publish(
            scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor"
        ).id
        session.commit()
    hidden = replace(scope, **{different: str(uuid4())})
    with Session(engine) as session:
        repository = SkillRepository(session)
        assert repository.get(hidden, skill_id) is None
        assert repository.list(hidden) == []
        assert repository.get_version(hidden, skill_id, version_id) is None
        package, stored = package_inputs(hidden, skill_id)
        with pytest.raises(SkillResourceNotFound):
            repository.save_draft(hidden, skill_id, expected_revision=2, package=package, stored=stored)
        with pytest.raises(SkillResourceNotFound):
            repository.publish(hidden, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")


def test_stale_revision_cannot_overwrite_draft(engine, scope):
    from app.skills.models import SkillDraft
    from app.skills.repository import SkillRepository, SkillRevisionConflict

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        session.commit()
    with Session(engine) as session:
        package, stored = package_inputs(scope, skill_id, "Edited instructions")
        draft = SkillRepository(session).save_draft(scope, skill_id, expected_revision=1, package=package, stored=stored)
        assert draft.revision == 2
        session.commit()
    with Session(engine) as session:
        with pytest.raises(SkillRevisionConflict):
            SkillRepository(session).save_draft(scope, skill_id, expected_revision=1, package=package, stored=stored)
        assert session.get(SkillDraft, skill_id).content.endswith("Edited instructions")


def test_published_snapshot_survives_draft_edits_and_replays(engine, scope):
    from app.skills.models import SkillDraft
    from app.skills.repository import SkillRepository, SkillIdempotencyConflict, SkillRevisionConflict

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        repository = SkillRepository(session)
        version = repository.publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")
        version_id = version.id
        assert version.version == 1
        assert session.get(SkillDraft, skill_id).revision == 2
        assert repository.get(scope, skill_id).published_version_id == version_id
        with pytest.raises(SkillRevisionConflict):
            repository.publish(scope, skill_id, expected_revision=1, idempotency_key="other", published_by="editor")
        package, stored = package_inputs(scope, skill_id, "Replacement instructions")
        repository.save_draft(scope, skill_id, expected_revision=2, package=package, stored=stored)
        session.commit()
    with Session(engine) as session:
        repository = SkillRepository(session)
        replay = repository.publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")
        assert replay.id == version_id
        assert replay.content.endswith("Initial instructions")
        assert [item["path"] for item in replay.files] == ["SKILL.md", "references/rules.txt"]
        assert replay.display_version == "1.0"
        assert replay.description == "Review reservoir"
        assert replay.package_digest != stored.package_digest
        assert replay.object_key != stored.object_key
        for revision, actor in [(3, "editor"), (1, "other-editor")]:
            with pytest.raises(SkillIdempotencyConflict):
                repository.publish(scope, skill_id, expected_revision=revision, idempotency_key="first", published_by=actor)
        second = repository.publish(scope, skill_id, expected_revision=3, idempotency_key="second", published_by="editor")
        assert second.version == 2
        assert second.content.endswith("Replacement instructions")


def test_caller_rollback_reverts_version_pointer_and_revision(engine, scope):
    from app.skills.models import SkillDraft, SkillVersion
    from app.skills.repository import SkillRepository

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        session.commit()
        SkillRepository(session).publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")
        session.rollback()
    with Session(engine) as session:
        assert SkillRepository(session).get(scope, skill_id).published_version_id is None
        assert session.get(SkillDraft, skill_id).revision == 1
        assert session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id)) is None


def test_create_is_not_committed_by_repository(engine, scope):
    from app.skills.repository import SkillRepository

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        session.rollback()
    with Session(engine) as reader:
        assert SkillRepository(reader).get(scope, skill_id) is None


def test_publishing_refreshes_draft_cached_before_another_editor_commit(engine, scope):
    from app.skills.models import SkillDraft
    from app.skills.repository import SkillRepository

    with Session(engine) as setup:
        skill_id = create_skill(setup, scope).id
        setup.commit()
    with Session(engine, expire_on_commit=False) as publisher:
        cached_draft = publisher.get(SkillDraft, skill_id)
        publisher.commit()
        with Session(engine) as editor:
            package, stored = package_inputs(scope, skill_id, "Committed by other editor")
            SkillRepository(editor).save_draft(scope, skill_id, expected_revision=1, package=package, stored=stored)
            editor.commit()
        assert cached_draft.revision == 1
        version = SkillRepository(publisher).publish(scope, skill_id, expected_revision=2, idempotency_key="first", published_by="editor")
        assert version.content.endswith("Committed by other editor")
        assert version.package_digest == stored.package_digest


def test_list_paginates_without_exposing_other_scopes(engine, scope):
    from app.skills.repository import SkillRepository, SkillScope

    with Session(engine) as session:
        skill_ids = {create_skill(session, scope, name=f"skill-{index}").id for index in range(3)}
        create_skill(session, SkillScope(scope.unit_id, str(uuid4())))
        session.commit()
    with Session(engine) as session:
        repository = SkillRepository(session)
        pages = [repository.list(scope, offset=index, limit=1) for index in range(3)]
        assert {page[0].id for page in pages} == skill_ids
        assert repository.list(scope, offset=3, limit=1) == []
        assert repository.list(scope, limit=0) == []


def test_get_version_requires_both_owning_skill_and_scope(engine, scope):
    from app.skills.repository import SkillRepository

    with Session(engine) as session:
        first = create_skill(session, scope)
        second = create_skill(session, scope, name="another-skill")
        repository = SkillRepository(session)
        version = repository.publish(scope, first.id, expected_revision=1, idempotency_key="first", published_by="editor")
        assert repository.get_version(scope, first.id, version.id).content.endswith("Initial instructions")
        assert repository.get_version(scope, second.id, version.id) is None


def test_rejects_foreign_published_version_pointer(engine, scope):
    from app.skills.repository import SkillRepository

    with Session(engine) as session:
        first = create_skill(session, scope)
        second = create_skill(session, scope, name="another-skill")
        version = SkillRepository(session).publish(scope, first.id, expected_revision=1, idempotency_key="first", published_by="editor")
        session.commit()
        second.published_version_id = version.id
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("operation", ["update", "delete", "bulk_update", "bulk_delete"])
def test_orm_rejects_published_version_changes(engine, scope, operation):
    from app.skills.models import SkillVersion, SkillVersionImmutableError
    from app.skills.repository import SkillRepository

    with Session(engine) as session:
        skill_id = create_skill(session, scope).id
        version = SkillRepository(session).publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")
        session.commit()
        with pytest.raises(SkillVersionImmutableError):
            if operation == "update":
                version.content = "Tampered"
            elif operation == "delete":
                session.delete(version)
            elif operation == "bulk_update":
                session.execute(update(SkillVersion).where(SkillVersion.id == version.id).values(content="Tampered"))
            else:
                session.execute(delete(SkillVersion).where(SkillVersion.id == version.id))
            session.flush()


@pytest.mark.parametrize("mismatch", ["unit", "project", "skill", "digest", "uuid"])
def test_rejects_mismatched_storage_or_invalid_identity(engine, scope, mismatch):
    from app.skills.repository import SkillRepository

    skill_id = str(uuid4())
    package, stored = package_inputs(scope, skill_id)
    if mismatch == "digest":
        stored = replace(stored, package_digest="0" * 64)
    elif mismatch == "uuid":
        skill_id = "not-a-uuid"
    else:
        parts = stored.object_key.split("/")
        parts[{"unit": 0, "project": 1, "skill": 2}[mismatch]] = str(uuid4())
        stored = replace(stored, object_key="/".join(parts))
    with Session(engine) as session, pytest.raises(ValueError):
        SkillRepository(session).create(scope, skill_id=skill_id, name=package.name, created_by="editor", package=package, stored=stored)


@pytest.mark.parametrize("module", ["app.db.base", "app.skills.models", "app.skills.repository"])
def test_models_register_in_fresh_process_for_any_import_order(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; from app.db.base import Base; from sqlalchemy.orm import configure_mappers; configure_mappers(); assert 'skill_versions' in Base.metadata.tables"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stderr
