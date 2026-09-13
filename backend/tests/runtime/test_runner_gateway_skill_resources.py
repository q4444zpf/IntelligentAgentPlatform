import base64
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotSkill,
    SnapshotSkillFile,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.runner_gateway_client import RunnerGatewayClient
from app.runtime.runner_gateway_service import RunnerGatewayService
from app.runtime.run_tokens import RunTokenClaims


def _file(path: str, data: bytes) -> SnapshotSkillFile:
    return SnapshotSkillFile(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_base64=base64.b64encode(data).decode(),
    )


def _snapshot() -> StoredExecutionSnapshot:
    skill = SnapshotSkill(name="forecast", version_id="v1", files=(_file("SKILL.md", b"hello"),))
    payload = ExecutionSnapshotPayload(
        snapshot_id="snap", run_id="run", unit_id="unit", project_id="project", user_id="user",
        actor=PublishedAgentSnapshot(id="a", name="a", description="", runtime_form="common", language="zh", system_prompt="", context_prompt="", approval_policy="never"),
        model=SnapshotModelSelection(provider_id="p", model="m"), messages=(), skills=(skill,),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=100000), created_at=datetime.now(UTC),
    )
    return StoredExecutionSnapshot(snapshot_id="snap", run_id="run", digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(), payload=payload, created_at=payload.created_at, expires_at=None)


class FakeSnapshotService:
    def __init__(self, stored): self.stored = stored
    def get(self, snapshot_id): return self.stored if snapshot_id == self.stored.snapshot_id else None


def _claims(stored):
    return RunTokenClaims(iss="iap-api", aud="iap-runner-gateway", jti="jti", run_id="run", unit_id="unit", project_id="project", snapshot_id=stored.snapshot_id, snapshot_digest=stored.digest, actions=("skill.resource.read",), iat=1, nbf=1, exp=9999999999)


def test_gateway_service_reads_exact_embedded_skill_file():
    stored = _snapshot()
    response = RunnerGatewayService(FakeSnapshotService(stored)).read_skill_file("run", "forecast", "SKILL.md", _claims(stored))
    assert response.skill_name == "forecast"
    assert base64.b64decode(response.data_base64) == b"hello"


def test_client_decodes_skill_file_response():
    request = type("Request", (), {"gateway_url": "http://gateway", "run_id": "run", "run_token": "token", "snapshot_digest": "a" * 64, "deadline_at": None, "execution_deadline_at": None})()
    client = RunnerGatewayClient.from_execution_request(request, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"skill_name":"forecast","path":"SKILL.md","size":5,"sha256":hashlib.sha256(b"hello").hexdigest(),"data_base64":base64.b64encode(b"hello").decode()})))
    value = client.read_skill_file("forecast", "SKILL.md")
    assert value["data"] == b"hello"


def test_gateway_rejects_object_skill_without_archive_metadata():
    stored = _snapshot()
    skill = SnapshotSkill(name="forecast", version_id="v1", object_key="u/p/s/x.zip", package_digest="a" * 64, files=(_file("SKILL.md", b"hello").model_copy(update={"content_base64": None}),))
    payload = stored.payload.model_copy(update={"skills": (skill,)})
    stored = stored.model_copy(update={"payload": payload, "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()})
    with pytest.raises(Exception) as error:
        RunnerGatewayService(FakeSnapshotService(stored), skill_package_storage=object()).read_skill_file("run", "forecast", "SKILL.md", _claims(stored))
    assert getattr(error.value, "code", None) == "skill_resource_unavailable"


def test_client_rejects_response_for_different_skill_identity():
    request = type("Request", (), {"gateway_url": "http://gateway", "run_id": "run", "run_token": "token", "snapshot_digest": "a" * 64, "deadline_at": None, "execution_deadline_at": None})()
    client = RunnerGatewayClient.from_execution_request(request, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"skill_name":"other","path":"SKILL.md","size":5,"sha256":hashlib.sha256(b"hello").hexdigest(),"data_base64":base64.b64encode(b"hello").decode()})))
    with pytest.raises(Exception) as error:
        client.read_skill_file("forecast", "SKILL.md")
    assert getattr(error.value, "code", None) == "runner_gateway_response_invalid"


@pytest.mark.parametrize(
    ("skill_name", "path", "expected"),
    [("missing", "SKILL.md", "skill_resource_not_found"), ("forecast", "missing.txt", "skill_resource_not_found")],
)
def test_gateway_rejects_unknown_skill_or_path(skill_name, path, expected):
    stored = _snapshot()
    with pytest.raises(Exception) as error:
        RunnerGatewayService(FakeSnapshotService(stored)).read_skill_file("run", skill_name, path, _claims(stored))
    assert getattr(error.value, "code", None) == expected


def test_gateway_rejects_disabled_skill():
    stored = _snapshot()
    payload = stored.payload.model_copy(update={"skills": (stored.payload.skills[0].model_copy(update={"enabled": False}),)})
    stored = stored.model_copy(update={"payload": payload, "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()})
    with pytest.raises(Exception) as error:
        RunnerGatewayService(FakeSnapshotService(stored)).read_skill_file("run", "forecast", "SKILL.md", _claims(stored))
    assert getattr(error.value, "code", None) == "skill_resource_disabled"
