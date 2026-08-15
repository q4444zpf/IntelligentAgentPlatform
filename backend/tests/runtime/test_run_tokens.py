from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    StoredExecutionSnapshot,
)
from app.runtime.run_tokens import (
    RuntimeRunTokenRevocation,
    RunTokenForbidden,
    RunTokenInvalid,
    RunTokenNotFound,
    RunTokenService,
    RunnerTokenSettings,
)


NOW = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
SIGNING_KEY = b"0123456789abcdef0123456789abcdef"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


@pytest.fixture
def snapshot():
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
        created_at=NOW,
    )
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest="a" * 64,
        payload=payload,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


@pytest.fixture
def token_service(session):
    return RunTokenService(
        session,
        signing_key=SIGNING_KEY,
        issuer="iap-api",
        audience="iap-runner-gateway",
        grace_seconds=30,
        clock=lambda: NOW,
    )


def test_token_is_run_action_and_digest_scoped(token_service, snapshot):
    issued = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )

    claims = token_service.verify(issued.value, "run-1", "snapshot.read")

    assert claims.snapshot_digest == snapshot.digest
    assert claims.snapshot_id == snapshot.snapshot_id
    assert claims.unit_id == "unit-1"
    assert claims.project_id == "project-1"
    with pytest.raises(RunTokenForbidden):
        token_service.verify(issued.value, "run-1", "tool.invoke")
    with pytest.raises(RunTokenNotFound):
        token_service.verify(issued.value, "run-2", "snapshot.read")


def test_revoked_tokens_are_rejected(token_service, snapshot):
    issued = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )
    token_service.revoke(snapshot.run_id, "cancelled")

    with pytest.raises(RunTokenInvalid, match="revoked"):
        token_service.verify(issued.value, snapshot.run_id, "snapshot.read")


def test_token_issued_after_run_revocation_is_valid(token_service, snapshot):
    old_token = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )
    token_service.clock = lambda: NOW + timedelta(seconds=1)
    token_service.revoke(snapshot.run_id, "approval_required")

    with pytest.raises(RunTokenInvalid, match="revoked"):
        token_service.verify(old_token.value, snapshot.run_id, "snapshot.read")

    token_service.clock = lambda: NOW + timedelta(seconds=2)
    resumed_token = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )

    claims = token_service.verify(
        resumed_token.value,
        snapshot.run_id,
        "snapshot.read",
    )
    assert claims.jti == resumed_token.claims.jti


def test_token_issued_later_in_same_second_after_revocation_is_valid(session, snapshot):
    service = RunTokenService(
        session,
        signing_key=SIGNING_KEY,
        issuer="iap-api",
        audience="iap-runner-gateway",
        grace_seconds=30,
        clock=lambda: NOW + timedelta(milliseconds=100),
    )
    service.revoke(snapshot.run_id, "approval_required")
    service.clock = lambda: NOW + timedelta(milliseconds=200)
    resumed_token = service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )

    claims = service.verify(
        resumed_token.value,
        snapshot.run_id,
        "snapshot.read",
    )

    assert claims.jti == resumed_token.claims.jti


def test_later_revocation_invalidates_resumed_token(token_service, snapshot):
    token_service.clock = lambda: NOW + timedelta(seconds=1)
    token_service.revoke(snapshot.run_id, "approval_required")
    token_service.clock = lambda: NOW + timedelta(seconds=2)
    resumed_token = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )
    token_service.clock = lambda: NOW + timedelta(seconds=3)
    token_service.revoke(snapshot.run_id, "completed")

    with pytest.raises(RunTokenInvalid, match="revoked"):
        token_service.verify(
            resumed_token.value,
            snapshot.run_id,
            "snapshot.read",
        )


def test_concurrent_first_revocations_are_idempotent(tmp_path, monkeypatch, snapshot):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'token-revocation.db'}")
    Base.metadata.create_all(engine)
    barrier = Barrier(2)
    thread_state = local()
    original_scalar = Session.scalar

    def synchronized_scalar(current_session, statement, *args, **kwargs):
        value = original_scalar(current_session, statement, *args, **kwargs)
        if not getattr(thread_state, "synchronized", False):
            thread_state.synchronized = True
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(Session, "scalar", synchronized_scalar)

    def revoke(reason):
        with Session(engine) as current_session:
            RunTokenService(
                current_session,
                signing_key=SIGNING_KEY,
                issuer="iap-api",
                audience="iap-runner-gateway",
                clock=lambda: NOW,
            ).revoke(snapshot.run_id, reason)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(revoke, "cancelled")
        second = executor.submit(revoke, "completed")
        first.result()
        second.result()

    with Session(engine) as verification_session:
        assert verification_session.query(RuntimeRunTokenRevocation).count() == 1
    engine.dispose()


def test_expired_tokens_are_rejected(session, snapshot):
    service = RunTokenService(
        session,
        signing_key=SIGNING_KEY,
        issuer="iap-api",
        audience="iap-runner-gateway",
        grace_seconds=0,
        clock=lambda: NOW,
    )
    issued = service.issue(snapshot, {"snapshot.read"}, NOW + timedelta(seconds=1))
    service.clock = lambda: NOW + timedelta(seconds=2)

    with pytest.raises(RunTokenInvalid, match="expired"):
        service.verify(issued.value, snapshot.run_id, "snapshot.read")


def test_token_with_another_audience_is_rejected(session, token_service, snapshot):
    issued = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )
    other_audience = RunTokenService(
        session,
        signing_key=SIGNING_KEY,
        issuer="iap-api",
        audience="another-service",
        clock=lambda: NOW,
    )

    with pytest.raises(RunTokenInvalid, match="audience"):
        other_audience.verify(issued.value, snapshot.run_id, "snapshot.read")


def test_token_errors_never_include_raw_token(token_service, snapshot):
    raw = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    ).value

    with pytest.raises(RunTokenInvalid) as captured:
        token_service.verify(raw + "broken", snapshot.run_id, "snapshot.read")

    assert raw not in str(captured.value)


def test_malformed_base64_is_mapped_to_safe_token_error(token_service, snapshot):
    issued = token_service.issue(
        snapshot, {"snapshot.read"}, NOW + timedelta(minutes=5)
    )
    header, payload, _signature = issued.value.split(".")

    with pytest.raises(RunTokenInvalid, match="invalid"):
        token_service.verify(f"{header}.{payload}.A", "run-1", "snapshot.read")


def test_settings_require_a_32_byte_signing_key(monkeypatch):
    monkeypatch.setenv("IAP_RUNNER_TOKEN_SIGNING_KEY", "short")

    with pytest.raises(ValueError, match="at least 32 bytes"):
        RunnerTokenSettings.from_env()
