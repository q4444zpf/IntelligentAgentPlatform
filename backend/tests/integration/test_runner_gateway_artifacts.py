import base64
import hashlib

from app.artifacts.models import ArtifactRecord


def _artifact_request(data=b"acceptance"):
    return {
        "path": "/artifacts/acceptance.txt",
        "content_type": "text/plain",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def test_artifact_bytes_are_downloadable_and_cross_run_access_is_hidden(
    runner_gateway_env,
):
    env = runner_gateway_env
    token = env.issue_token()
    created = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:create"),
        json=_artifact_request(),
    )
    artifact_id = created.json()["artifact_id"]
    downloaded = env.client.get(
        f"/internal/runner/runs/run-1/artifacts/{artifact_id}",
        headers=env.headers(token),
    )
    run_two_token = env.issue_token("run-2")
    cross_run = env.client.get(
        f"/internal/runner/runs/run-2/artifacts/{artifact_id}",
        headers=env.headers(run_two_token),
    )

    assert created.status_code == 201
    assert base64.b64decode(downloaded.json()["data_base64"]) == b"acceptance"
    assert cross_run.status_code == 404
    assert cross_run.json()["code"] == "artifact_not_found"


def test_artifact_upload_failure_rolls_back_database_and_object(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    env.storage.fail_put = True
    response = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:failed"),
        json=_artifact_request(),
    )

    assert response.status_code == 502
    assert response.json()["code"] == "artifact_upload_failed"
    assert "minio-secret" not in response.text
    assert env.session.query(ArtifactRecord).count() == 0
    assert env.storage.objects == {}


def test_artifact_event_failure_compensates_uploaded_object(
    runner_gateway_env,
    monkeypatch,
):
    env = runner_gateway_env
    token = env.issue_token()
    original_append_event = env.repository.append_event

    def fail_artifact_event(run_id, event_type, payload):
        if event_type == "artifact.ready":
            raise RuntimeError("database-password=secret")
        return original_append_event(run_id, event_type, payload)

    monkeypatch.setattr(env.repository, "append_event", fail_artifact_event)
    response = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:event-failed"),
        json=_artifact_request(),
    )

    assert response.status_code == 502
    assert response.json()["code"] == "artifact_upload_failed"
    assert "database-password" not in response.text
    assert env.session.query(ArtifactRecord).count() == 0
    assert env.storage.objects == {}


def test_artifact_digest_mismatch_is_rejected_without_upload(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    request = {**_artifact_request(), "sha256": "0" * 64}

    response = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:bad-digest"),
        json=request,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "artifact_invalid"
    assert env.session.query(ArtifactRecord).count() == 0
    assert env.storage.objects == {}


def test_artifact_idempotency_does_not_duplicate_minio_objects(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    request = _artifact_request()
    first = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:once"),
        json=request,
    )
    replay = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:once"),
        json=request,
    )

    assert replay.json() == first.json()
    assert env.session.query(ArtifactRecord).count() == 1
    assert len(env.storage.objects) == 1
