import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.runtime.checkpoint_store import CheckpointStore


def build_store(*, max_bytes=2097152):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return CheckpointStore(Session(engine), max_bytes=max_bytes)


def test_checkpoint_store_saves_and_loads_latest_state_per_run():
    store = build_store()

    store.save("run-1", "approval-1", {"status": "waiting_approval", "messages": [{"role": "user"}]})
    store.save("run-1", "approval-1", {"status": "queued", "messages": [{"role": "assistant"}]})
    store.save("run-2", "approval-1", {"status": "completed"})

    assert store.load_latest("run-1") == {"status": "queued", "messages": [{"role": "assistant"}]}
    assert store.load_latest("run-2") == {"status": "completed"}
    assert store.load_latest("missing") is None


def test_checkpoint_store_rejects_non_json_state():
    store = build_store()

    try:
        store.save("run-1", "bad", {"file": b"binary"})
    except ValueError as error:
        assert str(error) == "checkpoint state must be JSON serializable"
    else:
        raise AssertionError("expected non-json checkpoint to fail")


def test_checkpoint_store_persists_snapshot_digest_and_idempotency_key():
    store = build_store()

    store.save(
        "run-1",
        "step-1",
        {"status": "running"},
        snapshot_digest="a" * 64,
        idempotency_key="checkpoint-1",
    )

    checkpoint = store.load_latest_record("run-1")
    assert checkpoint is not None
    assert checkpoint.checkpoint_key == "step-1"
    assert checkpoint.snapshot_digest == "a" * 64
    assert checkpoint.idempotency_key == "checkpoint-1"
    assert checkpoint.state == {"status": "running"}


def test_checkpoint_store_rejects_state_over_configured_size_limit():
    store = build_store(max_bytes=32)

    with pytest.raises(ValueError, match="checkpoint state exceeds size limit"):
        store.save(
            "run-1",
            "step-1",
            {"content": "x" * 64},
            snapshot_digest="a" * 64,
            idempotency_key="checkpoint-1",
        )
