import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.identity.models import Project, Unit, User
from app.skills.models import Skill, SkillDraft
from app.skills.package_storage import SkillPackageStorage
from app.skills.project_errors import ProjectSkillError
from app.skills.project_schemas import ProjectSkillCreate, ProjectSkillDraftUpdate
from app.skills.project_service import ProjectSkillService
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.skills.project_support import make_context, manifest, seed_skill
from backend.tests.skills.test_package_storage import MemoryS3
from backend.tests.skills.test_project_drafts import FailingAudit


@pytest.fixture(name="pg_environment")
def pg_environment():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated service PostgreSQL configuration is required")
    assert make_url(url).database == "iap_skill_control_test_20260908_a"
    engine = create_engine(url, hide_parameters=True)
    assert engine.dialect.name == "postgresql"
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    context = make_context(
        "skill.read",
        "skill.manage",
        unit_id=str(uuid4()),
        project_id=str(uuid4()),
        user_id=str(uuid4()),
    )
    with sessions.begin() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "20260908_27"
        )
        session.add(
            Unit(
                id=context.unit_id,
                code=context.unit_id,
                name="Skill test unit",
                status="active",
            )
        )
        session.add(
            User(id=context.user_id, display_name="Skill test actor", status="active")
        )
        session.flush()
        session.add(
            Project(
                id=context.project_id,
                unit_id=context.unit_id,
                code=context.project_id,
                name="Skill test project",
                status="active",
            )
        )
    try:
        yield sessions, context
    finally:
        with engine.begin() as connection:
            ids = select(Skill.id).where(
                Skill.unit_id == context.unit_id, Skill.project_id == context.project_id
            )
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.unit_id == context.unit_id,
                    AuditEvent.project_id == context.project_id,
                )
            )
            connection.execute(delete(SkillDraft).where(SkillDraft.skill_id.in_(ids)))
            connection.execute(
                delete(Skill).where(
                    Skill.unit_id == context.unit_id,
                    Skill.project_id == context.project_id,
                )
            )
            connection.execute(delete(Project).where(Project.id == context.project_id))
            connection.execute(delete(Unit).where(Unit.id == context.unit_id))
            connection.execute(delete(User).where(User.id == context.user_id))
        engine.dispose()


@pytest.fixture
def pg_storage():
    return SkillPackageStorage(MemoryS3(), "pg-skill-test")


def test_postgres_create_save_and_audit_commit_together(pg_environment, pg_storage):
    sessions, context = pg_environment
    service = ProjectSkillService(sessions, storage_factory=lambda: pg_storage)
    created = service.create(context, ProjectSkillCreate(content=manifest("s")))
    saved = service.save_draft(
        context,
        created.id,
        ProjectSkillDraftUpdate(expected_revision=1, content=manifest("s", "Changed")),
    )
    assert saved.revision == 2
    with sessions() as session:
        skill = session.get(Skill, created.id)
        assert skill.published_version_id is None
        assert session.get(SkillDraft, created.id).content == manifest("s", "Changed")
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.resource_id == created.id)
        ).all()
        assert {event.action for event in events} == {
            "skill.create",
            "skill.draft.save",
        }
        assert len(events) == 2
        assert all(
            event.user_id == context.user_id and event.auth_method == "dev_test"
            for event in events
        )


@pytest.mark.parametrize("operation", ["create", "save"])
def test_postgres_audit_failure_rolls_back_data_and_inserted_audit(
    pg_environment, pg_storage, operation
):
    sessions, context = pg_environment
    skill_id = (
        seed_skill(sessions, pg_storage, context) if operation == "save" else None
    )
    service = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage, audit_recorder=FailingAudit()
    )
    with pytest.raises(RuntimeError, match="audit failed"):
        if skill_id:
            service.save_draft(
                context,
                skill_id,
                ProjectSkillDraftUpdate(
                    expected_revision=1, content=manifest("s", "Changed")
                ),
            )
        else:
            service.create(context, ProjectSkillCreate(content=manifest("s")))
    with sessions() as session:
        assert (
            session.scalar(
                select(AuditEvent).where(AuditEvent.project_id == context.project_id)
            )
            is None
        )
        if skill_id:
            assert session.get(SkillDraft, skill_id).revision == 1
            assert session.get(SkillDraft, skill_id).content == manifest("s")
        else:
            assert (
                session.scalar(
                    select(Skill).where(Skill.project_id == context.project_id)
                )
                is None
            )


def test_postgres_storage_io_holds_neither_session_nor_skill_lock(
    pg_environment, pg_storage, monkeypatch
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    engine = sessions.kw["bind"]

    def check_unlocked():
        assert engine.pool.checkedout() == 0
        with engine.begin() as connection:
            connection.execute(
                select(Skill.id)
                .where(Skill.id == skill_id)
                .with_for_update(nowait=True)
            ).one()

    read, put = pg_storage.read, pg_storage.put

    def checked_read(*args):
        check_unlocked()
        return read(*args)

    def checked_put(*args):
        check_unlocked()
        return put(*args)

    monkeypatch.setattr(pg_storage, "read", checked_read)
    monkeypatch.setattr(pg_storage, "put", checked_put)
    saved = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage
    ).save_draft(
        context,
        skill_id,
        ProjectSkillDraftUpdate(expected_revision=1, content=manifest("s", "Changed")),
    )
    assert saved.revision == 2


def test_postgres_competing_saves_commit_one_revision_and_one_audit(
    pg_environment, pg_storage, monkeypatch
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    barrier = Barrier(2, timeout=10)
    put = pg_storage.put

    def synchronized_put(*args):
        stored = put(*args)
        barrier.wait()
        return stored

    monkeypatch.setattr(pg_storage, "put", synchronized_put)
    service = ProjectSkillService(sessions, storage_factory=lambda: pg_storage)

    def save(body):
        try:
            return service.save_draft(
                context,
                skill_id,
                ProjectSkillDraftUpdate(
                    expected_revision=1, content=manifest("s", body)
                ),
            ).content
        except ProjectSkillError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, ("One", "Two")))
    assert results.count("skill_revision_conflict") == 1
    with sessions() as session:
        draft = session.get(SkillDraft, skill_id)
        assert draft.revision == 2
        assert draft.content in results
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.resource_id == skill_id)
                ).all()
            )
            == 1
        )


def test_postgres_duplicate_race_maps_only_name_constraint(
    pg_environment, pg_storage, monkeypatch
):
    sessions, context = pg_environment
    barrier = Barrier(2, timeout=10)
    put = pg_storage.put

    def synchronized_put(*args):
        stored = put(*args)
        barrier.wait()
        return stored

    monkeypatch.setattr(pg_storage, "put", synchronized_put)
    service = ProjectSkillService(sessions, storage_factory=lambda: pg_storage)

    def create(_):
        try:
            return service.create(context, ProjectSkillCreate(content=manifest("s"))).id
        except ProjectSkillError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, range(2)))
    assert results.count("skill_name_conflict") == 1
    with sessions() as session:
        assert (
            len(
                session.scalars(
                    select(Skill).where(Skill.project_id == context.project_id)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.project_id == context.project_id
                    )
                ).all()
            )
            == 1
        )


def test_postgres_unknown_integrity_error_is_not_converted(pg_environment, pg_storage):
    sessions, context = pg_environment

    class InvalidAudit(AuditRecorder):
        def record(self, session, request):
            session.add(
                Skill(
                    id=str(uuid4()),
                    unit_id=None,
                    project_id=context.project_id,
                    name="invalid",
                    created_by=context.user_id,
                )
            )
            session.flush()

    service = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage, audit_recorder=InvalidAudit()
    )
    with pytest.raises(IntegrityError):
        service.create(context, ProjectSkillCreate(content=manifest("s")))
    with sessions() as session:
        assert (
            session.scalar(select(Skill).where(Skill.project_id == context.project_id))
            is None
        )
