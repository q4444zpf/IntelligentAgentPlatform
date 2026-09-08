from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.skills.models import SkillDraft, SkillVersion
from app.skills.repository import SkillIdempotencyConflict, SkillRepository, SkillRevisionConflict, SkillScope
from tests.skills.test_repository import create_skill, package_inputs


@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")
    engine = create_engine(url, hide_parameters=True)
    if engine.dialect.name != "postgresql":
        pytest.fail("Skill version integration requires PostgreSQL")
    yield engine
    engine.dispose()


@pytest.fixture
def scope():
    return SkillScope(str(uuid4()), str(uuid4()))


def test_migration_enforces_uuid_and_owned_published_pointer(pg_engine, scope):
    columns = {column["name"]: str(column["type"]) for column in inspect(pg_engine).get_columns("skills")}
    assert columns["id"] == "UUID"
    with Session(pg_engine) as session:
        first = create_skill(session, scope)
        second = create_skill(session, scope, name="another-skill")
        version = SkillRepository(session).publish(scope, first.id, expected_revision=1, idempotency_key="first", published_by="editor")
        session.commit()
        second.published_version_id = version.id
        with pytest.raises(IntegrityError) as error:
            session.flush()
        assert error.value.orig.diag.constraint_name == "fk_skills_owned_published_version"


@pytest.mark.parametrize("operation", ["UPDATE skill_versions SET content = 'tampered'", "DELETE FROM skill_versions"])
def test_direct_sql_cannot_change_published_versions(pg_engine, scope, operation):
    with Session(pg_engine) as session:
        skill_id = create_skill(session, scope).id
        version_id = SkillRepository(session).publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor").id
        session.commit()
    with pg_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            connection.execute(text(operation + " WHERE id = :id"), {"id": version_id})
        assert error.value.orig.sqlstate == "55000"
        connection.rollback()
        assert connection.scalar(text("SELECT content FROM skill_versions WHERE id = :id"), {"id": version_id}).endswith("Initial instructions")


def test_uncommitted_publish_and_rollback_are_atomic(pg_engine, scope):
    with Session(pg_engine) as owner:
        skill_id = create_skill(owner, scope).id
        owner.commit()
        SkillRepository(owner).publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor")
        with Session(pg_engine) as reader:
            assert SkillRepository(reader).get(scope, skill_id).published_version_id is None
            assert reader.scalar(select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill_id)) == 0
        owner.rollback()
    with Session(pg_engine) as reader:
        assert SkillRepository(reader).get(scope, skill_id).published_version_id is None
        assert reader.get(SkillDraft, skill_id).revision == 1
        assert reader.scalar(select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill_id)) == 0


@pytest.mark.parametrize("contender", ["same_key", "same_key_other_actor", "different_key", "save_draft"])
def test_competing_request_waits_then_replays_or_conflicts(pg_engine, scope, contender):
    barrier = Barrier(2, timeout=10)
    worker_pid = []
    with Session(pg_engine) as owner:
        skill_id = create_skill(owner, scope).id
        owner.commit()
        first_id = SkillRepository(owner).publish(scope, skill_id, expected_revision=1, idempotency_key="first", published_by="editor").id

        def compete():
            with Session(pg_engine) as session:
                session.execute(text("SET LOCAL lock_timeout = '10s'"))
                worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                barrier.wait()
                repository = SkillRepository(session)
                try:
                    if contender == "save_draft":
                        package, stored = package_inputs(scope, skill_id, "Concurrent edit")
                        repository.save_draft(scope, skill_id, expected_revision=1, package=package, stored=stored)
                    else:
                        version = repository.publish(scope, skill_id, expected_revision=1,
                                                     idempotency_key="second" if contender == "different_key" else "first",
                                                     published_by="other-editor" if contender == "same_key_other_actor" else "editor")
                        result = version.id
                    session.commit()
                    return result
                except SkillRevisionConflict:
                    session.rollback()
                    return "revision_conflict"
                except SkillIdempotencyConflict:
                    session.rollback()
                    return "idempotency_conflict"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(compete)
            barrier.wait()
            try:
                deadline = time.monotonic() + 5
                with pg_engine.connect() as observer:
                    while time.monotonic() < deadline:
                        blocked = observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": worker_pid[0]})
                        if blocked:
                            break
                        time.sleep(0.02)
                assert blocked, "The contender must actually wait on the publisher's row lock"
            finally:
                owner.commit()
            expected = {"same_key": first_id, "same_key_other_actor": "idempotency_conflict"}.get(contender, "revision_conflict")
            assert future.result(timeout=10) == expected
    with Session(pg_engine) as reader:
        assert reader.scalar(select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill_id)) == 1
        assert reader.get(SkillDraft, skill_id).revision == 2
        assert SkillRepository(reader).get(scope, skill_id).published_version_id == first_id
