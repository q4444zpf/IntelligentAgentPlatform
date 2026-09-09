import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, local
from time import monotonic
from uuid import uuid4

import pytest
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.identity.models import Project, Unit, User
from app.skills.models import Skill, SkillDraft, SkillVersion
from app.skills.package_storage import SkillPackageStorage, SkillPackageStorageError
from app.skills.project_errors import ProjectSkillError
from app.skills.project_schemas import (
    ProjectSkillCreate,
    ProjectSkillDraftUpdate,
    ProjectSkillPublish,
)
from app.skills.project_service import ProjectSkillService
from app.skills.repository import SkillRepository
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.skills.project_support import make_context, manifest, seed_skill
from backend.tests.skills.test_package_storage import MemoryS3
from backend.tests.skills.test_project_drafts import FailingAudit
from backend.tests.skills.test_project_import import FailSecondAudit, two_packages


@pytest.fixture(name="pg_environment")
def pg_environment():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated service PostgreSQL configuration is required")
    assert make_url(url).database == "iap_skill_control_test_20260908_a"
    engine = create_engine(url, hide_parameters=True)
    assert engine.dialect.name == "postgresql"
    extra_user_ids = []
    sessions = sessionmaker(
        engine,
        expire_on_commit=False,
        class_=Session,
        info={"extra_user_ids": extra_user_ids},
    )
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
            # Committed versions forbid DELETE even in tests. Retain their UUID
            # scopes in the dedicated database, as the version integration suite does.
            if (
                connection.scalar(
                    select(SkillVersion.id)
                    .where(SkillVersion.skill_id.in_(ids))
                    .limit(1)
                )
                is None
            ):
                connection.execute(
                    delete(AuditEvent).where(
                        AuditEvent.unit_id == context.unit_id,
                        AuditEvent.project_id == context.project_id,
                    )
                )
                connection.execute(
                    delete(SkillDraft).where(SkillDraft.skill_id.in_(ids))
                )
                connection.execute(
                    delete(Skill).where(
                        Skill.unit_id == context.unit_id,
                        Skill.project_id == context.project_id,
                    )
                )
                connection.execute(
                    delete(Project).where(Project.id == context.project_id)
                )
                connection.execute(delete(Unit).where(Unit.id == context.unit_id))
                connection.execute(
                    delete(User).where(User.id.in_([context.user_id, *extra_user_ids]))
                )
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


@pytest.mark.parametrize("operation", ["create", "update"])
def test_postgres_import_competition_waits_on_database_and_commits_one_batch(
    pg_environment, pg_storage, monkeypatch, operation
):
    sessions, context = pg_environment
    entries = None
    if operation == "update":
        ids = [
            seed_skill(sessions, pg_storage, context, name=name) for name in ("a", "b")
        ]
        entries = json.dumps(
            [
                {
                    "action": "update",
                    "source_name": name,
                    "skill_id": skill_id,
                    "expected_revision": 1,
                }
                for name, skill_id in zip(("a", "b"), ids)
            ]
        )
    storage_barrier = Barrier(2, timeout=15)
    first_holds_transaction = Event()
    both_entered_write = Event()
    release_first = Event()
    state_lock = Lock()
    thread_state = local()
    pids = set()
    put = pg_storage.put

    def synchronized_put(*args):
        stored = put(*args)
        thread_state.put_count = getattr(thread_state, "put_count", 0) + 1
        if thread_state.put_count == 2:
            storage_barrier.wait()
        return stored

    method_name = "create" if operation == "create" else "lock_skill"
    repository_method = getattr(SkillRepository, method_name)

    def track_write(repository, *args, **kwargs):
        pid = repository._session.scalar(text("SELECT pg_backend_pid()"))
        with state_lock:
            pids.add(pid)
            if len(pids) == 2:
                both_entered_write.set()
        return repository_method(repository, *args, **kwargs)

    class HoldFirstAudit(AuditRecorder):
        def record(self, session, request):
            result = super().record(session, request)
            if not first_holds_transaction.is_set():
                first_holds_transaction.set()
                assert release_first.wait(
                    15
                ), "Observer did not release first transaction"
            return result

    monkeypatch.setattr(pg_storage, "put", synchronized_put)
    monkeypatch.setattr(SkillRepository, method_name, track_write)
    service = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage, audit_recorder=HoldFirstAudit()
    )

    def run_import():
        try:
            result = service.import_bundle(context, two_packages(), entries)
            return result.created_count + result.updated_count
        except ProjectSkillError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(run_import) for _ in range(2)]
        try:
            assert both_entered_write.wait(15)
            assert first_holds_transaction.wait(15)
            blocked = False
            deadline = monotonic() + 10
            with sessions.kw["bind"].connect() as observer:
                while monotonic() < deadline:
                    for pid in tuple(pids):
                        blockers = observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}
                        )
                        if set(blockers).intersection(pids):
                            blocked = True
                            break
                    if blocked:
                        break
                    release_first.wait(0.01)
            assert blocked, "Expected a real PostgreSQL unique/row-lock wait"
        finally:
            release_first.set()
        results = [future.result(timeout=20) for future in futures]
    assert results.count(2) == 1
    assert (
        results.count(
            "skill_name_conflict"
            if operation == "create"
            else "skill_revision_conflict"
        )
        == 1
    )
    with sessions() as session:
        skills = session.scalars(
            select(Skill).where(Skill.project_id == context.project_id)
        ).all()
        assert len(skills) == 2
        assert all(
            session.get(SkillDraft, skill.id).revision
            == (1 if operation == "create" else 2)
            for skill in skills
        )
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.project_id == context.project_id)
        ).all()
        assert len(events) == 2
        assert {item.action for item in events} == {"skill.import"}
        assert len({item.trace_id for item in events}) == 1


def test_postgres_import_uploads_without_sessions_or_locks(
    pg_environment, pg_storage, monkeypatch
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context, name="b")
    engine = sessions.kw["bind"]
    put = pg_storage.put

    def checked_put(*args):
        assert engine.pool.checkedout() == 0
        with engine.begin() as connection:
            connection.execute(
                select(Skill.id)
                .where(Skill.id == skill_id)
                .with_for_update(nowait=True)
            ).one()
        return put(*args)

    monkeypatch.setattr(pg_storage, "put", checked_put)
    result = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage
    ).import_bundle(
        context,
        two_packages(),
        json.dumps(
            [
                {"action": "create", "source_name": "a"},
                {
                    "action": "update",
                    "source_name": "b",
                    "skill_id": skill_id,
                    "expected_revision": 1,
                },
            ]
        ),
    )
    assert (result.created_count, result.updated_count) == (1, 1)


def test_postgres_second_import_audit_failure_rolls_back_whole_batch(
    pg_environment, pg_storage
):
    sessions, context = pg_environment
    with pytest.raises(RuntimeError, match="second audit failed"):
        ProjectSkillService(
            sessions,
            storage_factory=lambda: pg_storage,
            audit_recorder=FailSecondAudit(),
        ).import_bundle(context, two_packages(), None)
    with sessions() as session:
        assert (
            session.scalar(select(Skill).where(Skill.project_id == context.project_id))
            is None
        )
        assert (
            session.scalar(
                select(AuditEvent).where(AuditEvent.project_id == context.project_id)
            )
            is None
        )


@pytest.mark.parametrize("competition", ["same-key", "different-key", "different-user"])
def test_postgres_publish_race_waits_for_lock_and_commits_one_version_and_audit(
    pg_environment, pg_storage, monkeypatch, competition
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    second_context = context
    second_user = None
    if competition == "different-user":
        second_user = str(uuid4())
        with sessions.begin() as session:
            session.add(
                User(id=second_user, display_name="Second publisher", status="active")
            )
        sessions.kw["info"]["extra_user_ids"].append(second_user)
        second_context = make_context(
            "skill.manage",
            unit_id=context.unit_id,
            project_id=context.project_id,
            user_id=second_user,
        )
    boundary = Barrier(2, timeout=15)
    first_audit = Event()
    release = Event()
    both_writes = Event()
    state_lock = Lock()
    pids = set()
    engine = sessions.kw["bind"]
    read = pg_storage.read

    def synchronized_read(*args):
        data = read(*args)
        boundary.wait()
        return data

    def track_lock(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        if "FOR UPDATE" in statement and "skills" in statement:
            with state_lock:
                pids.add(connection.connection.driver_connection.info.backend_pid)
                if len(pids) == 2:
                    both_writes.set()

    class HoldAudit(AuditRecorder):
        def record(self, session, request):
            result = super().record(session, request)
            first_audit.set()
            assert release.wait(15), "Observer did not release publication transaction"
            return result

    monkeypatch.setattr(pg_storage, "read", synchronized_read)
    event.listen(engine, "before_cursor_execute", track_lock)
    services = [
        ProjectSkillService(
            sessions, storage_factory=lambda: pg_storage, audit_recorder=HoldAudit()
        )
        for _ in range(2)
    ]

    def publish(index):
        try:
            return services[index].publish(
                context if index == 0 else second_context,
                skill_id,
                ProjectSkillPublish(expected_revision=1),
                f"key-{index}" if competition == "different-key" else "shared",
            )
        except ProjectSkillError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(publish, index) for index in range(2)]
            try:
                assert both_writes.wait(15)
                assert first_audit.wait(15)
                blocked = False
                deadline = monotonic() + 10
                with engine.connect() as observer:
                    while monotonic() < deadline:
                        with state_lock:
                            observed = tuple(pids)
                        for pid in observed:
                            blockers = observer.scalar(
                                text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}
                            )
                            if set(blockers).intersection(observed):
                                blocked = True
                                break
                        if blocked:
                            break
                        release.wait(0.01)
                assert blocked, "Publishers must contend on a real PostgreSQL row lock"
            finally:
                release.set()
                boundary.abort()
            results = [future.result(timeout=20) for future in futures]
        assert len(pids) == 2
        successful = [result for result in results if not isinstance(result, str)]
        if competition == "same-key":
            assert len(successful) == 2 and successful[0] == successful[1]
        else:
            assert len(successful) == 1
            assert (
                "skill_revision_conflict"
                if competition == "different-key"
                else "skill_idempotency_conflict"
            ) in results
        with sessions() as session:
            versions = session.scalars(
                select(SkillVersion).where(SkillVersion.skill_id == skill_id)
            ).all()
            audits = session.scalars(
                select(AuditEvent).where(AuditEvent.resource_id == skill_id)
            ).all()
            assert len(versions) == len(audits) == 1
            assert versions[0].content == manifest("s")
            assert (versions[0].version, versions[0].source_revision) == (1, 1)
            assert session.get(SkillDraft, skill_id).revision == 2
            assert (
                session.get(Skill, skill_id).published_version_id
                == versions[0].id
                == successful[0].id
            )
            assert audits[0].metadata_json == {
                "revision": 1,
                "digest": versions[0].package_digest,
                "version_id": versions[0].id,
            }
    finally:
        release.set()
        boundary.abort()
        event.remove(engine, "before_cursor_execute", track_lock)


@pytest.mark.parametrize("competitor", ["save", "publish", "publish-read-failure"])
def test_postgres_publish_rechecks_after_concurrent_storage_boundary(
    pg_environment, pg_storage, monkeypatch, competitor
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    captured = Event()
    release = Event()
    engine = sessions.kw["bind"]
    waiting_storage = SkillPackageStorage(pg_storage._client, pg_storage._bucket)
    read = waiting_storage.read

    def blocked_read(*args):
        assert engine.pool.checkedout() == 0
        with engine.begin() as connection:
            connection.execute(
                select(Skill.id)
                .where(Skill.id == skill_id)
                .with_for_update(nowait=True)
            ).one()
        data = read(*args)
        captured.set()
        assert release.wait(15)
        if competitor == "publish-read-failure":
            raise SkillPackageStorageError("read failed after competitor committed")
        return data

    monkeypatch.setattr(waiting_storage, "read", blocked_read)
    first = ProjectSkillService(sessions, storage_factory=lambda: waiting_storage)
    second = ProjectSkillService(sessions, storage_factory=lambda: pg_storage)
    request = ProjectSkillPublish(expected_revision=1)
    with ThreadPoolExecutor(max_workers=2) as workers:
        future = workers.submit(first.publish, context, skill_id, request, "shared")
        try:
            assert captured.wait(15)
            if competitor == "save":
                competing = workers.submit(
                    second.save_draft,
                    context,
                    skill_id,
                    ProjectSkillDraftUpdate(
                        expected_revision=1, content=manifest("s", "Changed")
                    ),
                )
            else:
                competing = workers.submit(
                    second.publish, context, skill_id, request, "shared"
                )
            committed = competing.result(timeout=15)
        finally:
            release.set()
        if competitor == "save":
            with pytest.raises(ProjectSkillError) as caught:
                future.result(timeout=15)
            assert caught.value.code == "skill_revision_conflict"
        else:
            assert future.result(timeout=15) == committed
    with sessions() as session:
        draft = session.get(SkillDraft, skill_id)
        assert draft.revision == 2
        versions = session.scalars(
            select(SkillVersion).where(SkillVersion.skill_id == skill_id)
        ).all()
        audits = session.scalars(
            select(AuditEvent).where(AuditEvent.resource_id == skill_id)
        ).all()
        assert len(audits) == 1
        if competitor == "save":
            assert versions == [] and draft.content == manifest("s", "Changed")
            assert session.get(Skill, skill_id).published_version_id is None
            assert audits[0].action == "skill.draft.save"
        else:
            assert len(versions) == 1 and versions[0].id == committed.id
            assert draft.content == manifest("s")
            assert session.get(Skill, skill_id).published_version_id == committed.id
            assert audits[0].action == "skill.publish"


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_postgres_publish_failure_preserves_snapshot_revision_pointer_and_audit(
    pg_environment, pg_storage, failure
):
    from app.skills.project_packages import snapshot_draft

    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    service = ProjectSkillService(sessions, storage_factory=lambda: pg_storage)
    first = service.publish(
        context, skill_id, ProjectSkillPublish(expected_revision=1), "first"
    )
    with sessions() as session:
        original = snapshot_draft(session.get(SkillDraft, skill_id))

    def fail_commit(session):
        assert (
            len(
                session.scalars(
                    select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                ).all()
            )
            == 2
        )
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.resource_id == skill_id)
                ).all()
            )
            == 2
        )
        raise RuntimeError("commit failed after audit insertion")

    if failure == "commit":
        event.listen(sessions, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="failed"):
            ProjectSkillService(
                sessions,
                storage_factory=lambda: pg_storage,
                audit_recorder=FailingAudit() if failure == "audit" else None,
            ).publish(
                context, skill_id, ProjectSkillPublish(expected_revision=2), "second"
            )
    finally:
        if failure == "commit":
            event.remove(sessions, "before_commit", fail_commit)
    with sessions() as session:
        assert snapshot_draft(session.get(SkillDraft, skill_id)) == original
        assert session.get(Skill, skill_id).published_version_id == first.id
        assert (
            len(
                session.scalars(
                    select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.resource_id == skill_id)
                ).all()
            )
            == 1
        )
    retried = service.publish(
        context, skill_id, ProjectSkillPublish(expected_revision=2), "second"
    )
    assert (retried.version, retried.source_revision) == (2, 2)


def test_postgres_publish_rechecks_replay_when_revision_turns_stale_after_preflight(
    pg_environment, pg_storage
):
    sessions, context = pg_environment
    skill_id = seed_skill(sessions, pg_storage, context)
    request = ProjectSkillPublish(expected_revision=1)
    observed_empty = Event()
    release = Event()
    state = local()
    pids = set()
    engine = sessions.kw["bind"]

    def pause_empty_result(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        if (
            getattr(state, "first_request", False)
            and not observed_empty.is_set()
            and (
                statement.startswith("SELECT")
                and "skill_versions.idempotency_key =" in statement
            )
        ):
            pids.add(connection.connection.driver_connection.info.backend_pid)
            observed_empty.set()
            assert release.wait(15)

    class TrackAudit(AuditRecorder):
        def record(self, session, request):
            pids.add(session.scalar(text("SELECT pg_backend_pid()")))
            return super().record(session, request)

    def unavailable():
        raise AssertionError(
            "Stale snapshot must recheck the now committed replay before storage"
        )

    first = ProjectSkillService(sessions, storage_factory=unavailable)
    second = ProjectSkillService(
        sessions, storage_factory=lambda: pg_storage, audit_recorder=TrackAudit()
    )

    def run_first():
        state.first_request = True
        return first.publish(context, skill_id, request, "same")

    event.listen(engine, "after_cursor_execute", pause_empty_result)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            future = workers.submit(run_first)
            try:
                assert observed_empty.wait(15)
                competing = workers.submit(
                    second.publish, context, skill_id, request, "same"
                )
                committed = competing.result(timeout=15)
            finally:
                release.set()
            assert future.result(timeout=15) == committed
        assert len(pids) == 2
    finally:
        release.set()
        event.remove(engine, "after_cursor_execute", pause_empty_result)
    with sessions() as session:
        assert session.get(SkillDraft, skill_id).revision == 2
        assert session.get(Skill, skill_id).published_version_id == committed.id
        assert (
            len(
                session.scalars(
                    select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.resource_id == skill_id)
                ).all()
            )
            == 1
        )
