import hashlib

import pytest
from app.artifacts.service import (
    ArtifactAlreadyExistsError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactSizeError,
)
from app.conversations.models import AgentRun, Conversation, Message
from app.db.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put_bytes(self, object_key, data, content_type):
        self.objects[object_key] = (data, content_type)

    def get_bytes(self, object_key):
        return self.objects[object_key][0]

    def delete_object(self, object_key):
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)


@pytest.fixture
def artifact_service():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    conversation = Conversation(
        unit_id="unit-1",
        project_id="project-1",
        owner_id="user-1",
        title="artifact run",
    )
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        content="create report",
    )
    session.add(message)
    session.flush()
    run = AgentRun(
        id="run-1",
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        actor_type="agent",
        actor_id="agent-1",
        status="running",
    )
    session.add(run)
    session.commit()
    storage = FakeStorage()
    return ArtifactService(session, storage, runner_max_bytes=8), storage


def test_create_for_run_derives_scope_and_uses_run_prefixed_object_key(
    artifact_service,
):
    service, storage = artifact_service
    data = b"report"

    artifact = service.create_for_run(
        run_id="run-1",
        path="reports/result.txt",
        content_type="text/plain",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )

    assert artifact.unit_id == "unit-1"
    assert artifact.project_id == "project-1"
    assert artifact.owner_id == "user-1"
    assert artifact.scope == "private"
    assert artifact.run_id == "run-1"
    assert artifact.object_key.startswith(
        "units/unit-1/projects/project-1/runs/run-1/"
    )
    assert storage.objects[artifact.object_key][0] == data


def test_create_for_run_enforces_size_checksum_and_create_only(artifact_service):
    service, _ = artifact_service
    data = b"report"
    digest = hashlib.sha256(data).hexdigest()

    service.create_for_run(
        run_id="run-1",
        path="result.txt",
        content_type="text/plain",
        data=data,
        sha256=digest,
    )

    with pytest.raises(ArtifactAlreadyExistsError):
        service.create_for_run(
            run_id="run-1",
            path="result.txt",
            content_type="text/plain",
            data=data,
            sha256=digest,
        )
    with pytest.raises(ArtifactSizeError):
        service.create_for_run(
            run_id="run-1",
            path="large.txt",
            content_type="text/plain",
            data=b"123456789",
            sha256=hashlib.sha256(b"123456789").hexdigest(),
        )
    with pytest.raises(ArtifactIntegrityError):
        service.create_for_run(
            run_id="run-1",
            path="bad.txt",
            content_type="text/plain",
            data=data,
            sha256="0" * 64,
        )


def test_get_and_list_for_run_never_expose_another_run_artifact(artifact_service):
    service, _ = artifact_service
    data = b"report"
    artifact = service.create_for_run(
        run_id="run-1",
        path="result.txt",
        content_type="text/plain",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )

    assert service.get_for_run("run-1", artifact.id).id == artifact.id
    assert [item.id for item in service.list_for_run("run-1")] == [artifact.id]
    with pytest.raises(ArtifactNotFoundError):
        service.get_for_run("run-2", artifact.id)
    assert service.list_for_run("run-2") == []
