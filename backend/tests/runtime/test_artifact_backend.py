import base64
import hashlib
from datetime import UTC, datetime

import pytest
from app.runtime.artifact_backend import (
    ArtifactBackend,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeArtifactClient:
    def __init__(self):
        self.files = {}

    def create_artifact(self, **request):
        path = request["path"]
        artifact_id = f"artifact-{len(self.files) + 1}"
        item = {
            "path": path,
            "artifact_id": artifact_id,
            "size_bytes": len(request["data"]),
            "sha256": request["sha256"],
            "content_type": request["content_type"],
            "data": request["data"],
        }
        self.files[path] = item
        return item

    def list_artifacts(self):
        return list(self.files.values())

    def read_artifact(self, artifact_id):
        return next(item for item in self.files.values() if item["artifact_id"] == artifact_id)


@pytest.mark.parametrize(
    "path",
    ["../secret", "C:/secret", "\\\\host\\share", "/artifacts/a\x00.txt"],
)
def test_artifact_backend_rejects_unsafe_paths(path):
    backend = ArtifactBackend(FakeArtifactClient())

    result = backend.write(path, "data")

    assert result.error == "Artifact path is invalid"


def test_artifact_backend_writes_reads_and_lists_virtual_files():
    client = FakeArtifactClient()
    backend = ArtifactBackend(client)

    created = backend.write("/artifacts/reports/result.txt", "result")

    assert created.path == "/artifacts/reports/result.txt"
    assert created.error is None
    read = backend.read(created.path)
    assert read.error is None
    assert read.file_data["content"] == "result"
    assert client.files[created.path]["content_type"] == "text/plain"
    listed = backend.ls("/artifacts/reports")
    assert [entry["path"] for entry in listed.entries] == [created.path]
    assert [item.path for item in backend.list("/artifacts/reports")] == [created.path]


def test_artifact_backend_maps_deepagents_relative_paths_into_artifacts():
    client = FakeArtifactClient()
    backend = ArtifactBackend(client)

    created = backend.write("acceptance-result.txt", "accepted")

    assert created.error is None
    assert created.path == "/artifacts/acceptance-result.txt"
    assert list(client.files) == ["/artifacts/acceptance-result.txt"]


def test_deepagents_write_file_maps_virtual_root_into_artifacts():
    from app.runtime.deepagents_factory import (
        DeepAgentFactory,
        PublishedAgentSnapshot as FactoryPublishedAgentSnapshot,
    )
    from app.runtime.gateway_model import GatewayChatModel

    class ModelTransport:
        def __init__(self):
            self.calls = 0

        def invoke_model(self, _request, _idempotency_key):
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": "Creating the artifact.",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "write_file",
                            "arguments": {
                                "file_path": "acceptance-result.txt",
                                "content": "accepted",
                            },
                        }
                    ],
                }
            return {
                "content": "Artifact created.",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "tool_calls": [],
            }

    client = FakeArtifactClient()
    graph = DeepAgentFactory().build(
        FactoryPublishedAgentSnapshot("agent-1", "Agent", "system", "", ()),
        model=GatewayChatModel(ModelTransport()),
        tools=[],
        backend=ArtifactBackend(client),
    )

    graph.invoke({"messages": [{"role": "user", "content": "create a file"}]})

    assert list(client.files) == ["/artifacts/acceptance-result.txt"]


@pytest.mark.parametrize("root_alias", ["/", "."])
def test_artifact_backend_maps_deepagents_root_aliases_to_artifacts(root_alias):
    backend = ArtifactBackend(FakeArtifactClient())
    backend.write("/artifacts/result.txt", "result")

    listed = backend.ls(root_alias)

    assert listed.error is None
    assert [entry["path"] for entry in listed.entries] == [
        "/artifacts/result.txt"
    ]


def test_artifact_backend_is_create_only():
    backend = ArtifactBackend(FakeArtifactClient())
    backend.write("/artifacts/result.txt", "first")

    duplicate = backend.write("/artifacts/result.txt", "second")
    edited = backend.edit("/artifacts/result.txt", "first", "second")
    deleted = backend.delete("/artifacts/result.txt")

    assert duplicate.error == "Artifact already exists"
    assert edited.error == "Artifacts are immutable"
    assert deleted.error == "Artifact deletion requires platform authorization"


class GatewayStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, object_key, data, content_type):
        self.objects[object_key] = (data, content_type)

    def get_bytes(self, object_key):
        return self.objects[object_key][0]

    def delete_object(self, object_key):
        self.objects.pop(object_key, None)


class GatewayTokenService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def verify(self, token, run_id, action):
        from app.runtime.run_tokens import RunTokenClaims, RunTokenForbidden

        if token == "denied":
            raise RunTokenForbidden("forbidden")
        return RunTokenClaims(
            iss="iap-api",
            aud="iap-runner-gateway",
            jti="token-1",
            run_id=run_id,
            unit_id="unit-1",
            project_id="project-1",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_digest=self.snapshot.digest,
            actions=("artifact.create",),
            iat=1,
            nbf=1,
            exp=9999999999,
        )


class GatewaySnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, snapshot_id):
        return self.snapshot if snapshot_id == self.snapshot.snapshot_id else None


def _gateway_snapshot():
    from app.runtime.execution_snapshot import (
        ExecutionSnapshotPayload,
        PublishedAgentSnapshot,
        SnapshotModelSelection,
        SnapshotRuntimeLimits,
        StoredExecutionSnapshot,
        canonical_snapshot_bytes,
    )

    payload = ExecutionSnapshotPayload(
        snapshot_id="snapshot-1",
        run_id="run-1",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
        created_at=payload.created_at,
        expires_at=None,
    )


def _add_run(session, run_id, project_id="project-1"):
    from app.conversations.models import AgentRun, Conversation, Message, RunEvent

    conversation = Conversation(
        unit_id="unit-1",
        project_id=project_id,
        owner_id="user-1",
        title=run_id,
    )
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        content="create artifact",
    )
    session.add(message)
    session.flush()
    session.add(
        AgentRun(
            id=run_id,
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="agent-1",
            status="running",
        )
    )
    session.commit()


def _gateway_client(repository_type=None):
    from app.artifacts.models import ArtifactRecord
    from app.artifacts.service import ArtifactService
    from app.conversations.models import AgentRun, Conversation, Message, RunEvent
    from app.conversations.repository import ConversationRepository
    from app.db.base import Base
    from app.runtime.run_tokens import RunTokenClaims
    from app.runtime.runner_gateway_auth import (
        RunnerGatewayError,
        runner_gateway_error_handler,
    )
    from app.runtime.runner_gateway_router import create_router
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    if repository_type is None:
        repository_type = ConversationRepository

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    _add_run(session, "run-1")
    _add_run(session, "run-2")
    snapshot = _gateway_snapshot()
    storage = GatewayStorage()
    artifacts = ArtifactService(session, storage)
    repository = repository_type(session)
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(
        create_router(
            token_service_dependency=lambda: GatewayTokenService(snapshot),
            snapshot_service_dependency=lambda: GatewaySnapshotService(snapshot),
            conversation_repository_dependency=lambda: repository,
            artifact_service_dependency=lambda: artifacts,
        ),
        prefix="/internal/runner",
    )
    return TestClient(app), session, artifacts, storage


def _headers(token="valid", key=None):
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def test_runner_artifact_gateway_is_scoped_idempotent_and_emits_ready_event():
    from app.artifacts.models import ArtifactRecord
    from app.conversations.models import RunEvent
    from sqlalchemy import select

    client, session, _, _ = _gateway_client()
    data = b"result"
    request = {
        "path": "/artifacts/result.txt",
        "content_type": "text/plain",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }

    first = client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=_headers(key="artifact-1"),
        json=request,
    )
    replay = client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=_headers(key="artifact-1"),
        json=request,
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert session.query(ArtifactRecord).count() == 1
    events = list(
        session.scalars(
            select(RunEvent).where(RunEvent.run_id == "run-1")
        )
    )
    assert [(event.event_type, event.payload["path"]) for event in events] == [
        ("artifact.ready", "/artifacts/result.txt")
    ]

    listed = client.get(
        "/internal/runner/runs/run-1/artifacts", headers=_headers()
    )
    artifact_id = first.json()["artifact_id"]
    read = client.get(
        f"/internal/runner/runs/run-1/artifacts/{artifact_id}",
        headers=_headers(),
    )
    assert listed.json() == [first.json()]
    assert base64.b64decode(read.json()["data_base64"]) == data


def test_runner_cannot_read_other_run_artifact():
    client, _, artifacts, _ = _gateway_client()
    data = b"foreign"
    foreign = artifacts.create_for_run(
        run_id="run-2",
        path="foreign.txt",
        content_type="text/plain",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )

    response = client.get(
        f"/internal/runner/runs/run-1/artifacts/{foreign.id}",
        headers=_headers(),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "artifact_not_found"


def test_runner_artifact_routes_require_artifact_action():
    client, _, _, _ = _gateway_client()

    response = client.get(
        "/internal/runner/runs/run-1/artifacts", headers=_headers("denied")
    )

    assert response.status_code == 403


def test_runner_artifact_upload_removes_object_when_event_persistence_fails():
    from app.artifacts.models import ArtifactRecord
    from app.conversations.repository import ConversationRepository

    class FailingEventRepository(ConversationRepository):
        def append_event(self, run_id, event_type, payload):
            raise RuntimeError("database unavailable")

    client, session, _, storage = _gateway_client(FailingEventRepository)
    data = b"result"

    response = client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=_headers(key="artifact-failure"),
        json={
            "path": "/artifacts/result.txt",
            "content_type": "text/plain",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data_base64": base64.b64encode(data).decode("ascii"),
        },
    )

    assert response.status_code == 502
    assert response.json()["code"] == "artifact_upload_failed"
    assert session.query(ArtifactRecord).count() == 0
    assert storage.objects == {}
