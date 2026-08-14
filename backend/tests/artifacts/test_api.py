
from app.artifacts.router import create_router
from app.conversations.models import AgentRun, Conversation, Message
from app.db.base import Base
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, object_key, data, content_type):
        self.objects[object_key] = (data, content_type)

    def presigned_get_url(self, object_key, expires_seconds=900):
        return f"https://storage.test/{object_key}?expires={expires_seconds}"

    def delete_object(self, object_key):
        self.objects.pop(object_key, None)


def build_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    storage = FakeStorage()
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.state.artifact_session = session
    app.include_router(
        create_router(lambda _session: session, storage=storage), prefix="/api"
    )
    return TestClient(app), session, storage


HEADERS = {"X-Unit-ID": "unit-1", "X-User-ID": "u1", "X-Project-ID": "p1"}


def upload(client, *, headers=HEADERS, filename="report.txt", scope="project", run_id=None):
    data = {"scope": scope}
    if run_id:
        data["run_id"] = run_id
    return client.post(
        "/api/artifacts",
        files={"file": (filename, b"artifact bytes", "text/plain")},
        data=data,
        headers=headers,
    )


def test_upload_generates_server_owned_object_key_and_metadata():
    client, session, storage = build_client()

    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "report.txt"
    assert body["size_bytes"] == len(b"artifact bytes")
    record = session.get(__import__("app.artifacts.models", fromlist=["ArtifactRecord"]).ArtifactRecord, body["id"])
    assert record.object_key.startswith("units/unit-1/projects/p1/")
    assert record.object_key in storage.objects


def test_artifact_visibility_is_scoped_to_project_and_owner():
    client, _, _ = build_client()
    visible = upload(client).json()
    private = upload(client, scope="private").json()

    other_project = {"X-Unit-ID": "unit-1", "X-User-ID": "u2", "X-Project-ID": "p2"}
    assert client.get(f"/api/artifacts/{visible['id']}", headers=other_project).status_code == 404
    assert client.get(f"/api/artifacts/{private['id']}", headers=other_project).status_code == 404


def test_download_returns_short_lived_signed_url():
    client, _, _ = build_client()
    artifact = upload(client).json()

    response = client.get(f"/api/artifacts/{artifact['id']}/download?expires_in=3600", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["expires_in"] == 900
    assert "expires=900" in response.json()["url"]


def test_delete_artifact_removes_object_and_marks_record_deleted():
    client, session, storage = build_client()
    artifact = upload(client).json()
    object_key = session.get(__import__("app.artifacts.models", fromlist=["ArtifactRecord"]).ArtifactRecord, artifact["id"]).object_key

    response = client.delete(f"/api/artifacts/{artifact['id']}", headers=HEADERS)

    assert response.status_code == 200
    assert object_key not in storage.objects
    assert client.get(f"/api/artifacts/{artifact['id']}", headers=HEADERS).status_code == 404


def test_run_artifact_requires_run_in_current_project():
    client, session, _ = build_client()
    conversation = Conversation(unit_id="unit-1", project_id="p1", owner_id="u1", title="run")
    session.add(conversation)
    session.flush()
    message = Message(conversation_id=conversation.id, sequence=1, role="user", content="go")
    session.add(message)
    session.flush()
    run = AgentRun(conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="a", status="queued")
    session.add(run)
    session.commit()

    response = upload(client, run_id=run.id)
    assert response.status_code == 201
    assert response.json()["run_id"] == run.id

    foreign = {"X-Unit-ID": "unit-1", "X-User-ID": "u2", "X-Project-ID": "p2"}
    denied = upload(client, headers=foreign, run_id=run.id)
    assert denied.status_code == 404


def test_existing_artifact_can_be_attached_to_visible_run():
    client, session, _ = build_client()
    artifact = upload(client).json()
    conversation = Conversation(unit_id="unit-1", project_id="p1", owner_id="u1", title="run")
    session.add(conversation)
    session.flush()
    message = Message(conversation_id=conversation.id, sequence=1, role="user", content="go")
    session.add(message)
    session.flush()
    run = AgentRun(conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="a", status="queued")
    session.add(run)
    session.commit()

    response = client.post(f"/api/runs/{run.id}/artifacts/{artifact['id']}", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["run_id"] == run.id


def test_existing_artifact_cannot_be_attached_to_foreign_run():
    client, session, _ = build_client()
    artifact = upload(client).json()
    conversation = Conversation(unit_id="unit-1", project_id="p2", owner_id="u2", title="foreign")
    session.add(conversation)
    session.flush()
    message = Message(conversation_id=conversation.id, sequence=1, role="user", content="go")
    session.add(message)
    session.flush()
    run = AgentRun(conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="a", status="queued")
    session.add(run)
    session.commit()

    response = client.post(f"/api/runs/{run.id}/artifacts/{artifact['id']}", headers=HEADERS)

    assert response.status_code == 404
