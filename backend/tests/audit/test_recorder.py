from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.db.base import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def make_request(**overrides):
    values = {
        "unit_id": "unit-1",
        "category": "runtime",
        "source": "agent",
        "action": "forecast.run",
        "status": "succeeded",
        "risk_level": "low",
        "idempotency_key": "audit-key-1",
        "occurred_at": datetime(2026, 8, 3, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return AuditRecordRequest(**values)


def test_request_has_required_fields_and_safe_defaults():
    request = make_request()

    assert request.project_id is None
    assert request.user_id is None
    assert request.actor_role is None
    assert request.trace_id is None
    assert request.run_id is None
    assert request.parent_event_id is None
    assert request.resource_type is None
    assert request.resource_id is None
    assert request.resource_name is None
    assert request.summary == ""
    assert request.metadata == {}
    assert request.error_code is None
    assert request.duration_ms is None
    assert request.allowed_metadata_keys == frozenset()
    with pytest.raises(AttributeError):
        request.action = "changed"


def test_record_redacts_flushes_and_leaves_transaction_to_caller(session, monkeypatch):
    commit_calls = 0

    def forbidden_commit():
        nonlocal commit_calls
        commit_calls += 1
        raise AssertionError("recorder must not commit")

    monkeypatch.setattr(session, "commit", forbidden_commit)
    request = make_request(
        summary="<b>completed</b>",
        metadata={
            "nested": {"safe": "kept", "authorization": "private"},
            "env": {"API_KEY": "private"},
            "file_path": "/customer/project/result.nc",
            "raw_prompt": "must be discarded",
        },
        allowed_metadata_keys=frozenset({"nested", "env", "file_path"}),
        duration_ms=12,
    )

    event = AuditRecorder().record(session, request)

    assert event.id is not None
    assert event.summary == "&lt;b&gt;completed&lt;/b&gt;"
    assert event.metadata_json == {
        "nested": {"safe": "kept", "authorization": "[REDACTED]"},
        "env": "[REDACTED]",
        "file_path": "result.nc",
    }
    assert session.get(AuditEvent, event.id) is event
    assert event.actor_role == "unknown"
    assert session.in_transaction()
    assert commit_calls == 0


def test_record_is_idempotent_without_committing(session):
    recorder = AuditRecorder()
    first = recorder.record(session, make_request())
    second = recorder.record(session, make_request(summary="ignored duplicate"))

    assert second is first
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


@pytest.mark.parametrize("field", ["unit_id", "action", "idempotency_key"])
def test_record_rejects_empty_required_strings(session, field):
    with pytest.raises(ValueError, match=field):
        AuditRecorder().record(session, make_request(**{field: ""}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "operations"),
        ("source", "api"),
        ("status", "pending"),
        ("risk_level", "urgent"),
    ],
)
def test_record_rejects_values_outside_strict_enums(session, field, value):
    with pytest.raises(ValueError, match=field):
        AuditRecorder().record(session, make_request(**{field: value}))


@pytest.mark.parametrize("actor_role", ["admin", "agent", "user,project_admin", "user,user", ""])
def test_record_rejects_invalid_or_non_deterministic_actor_roles(session, actor_role):
    with pytest.raises(ValueError, match="actor_role"):
        AuditRecorder().record(session, make_request(actor_role=actor_role))


def test_record_preserves_deterministic_multi_role_snapshot(session):
    assert AuditRecorder().record(session, make_request(actor_role="project_admin,user")).actor_role == "project_admin,user"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit_id", "x" * 65),
        ("action", "x" * 101),
        ("idempotency_key", "x" * 181),
        ("resource_name", "x" * 201),
    ],
)
def test_record_rejects_values_longer_than_model_columns(session, field, value):
    with pytest.raises(ValueError, match=field):
        AuditRecorder().record(session, make_request(**{field: value}))


def test_record_rejects_naive_occurred_at_and_negative_duration(session):
    with pytest.raises(ValueError, match="occurred_at"):
        AuditRecorder().record(
            session, make_request(occurred_at=datetime(2026, 8, 3))
        )
    with pytest.raises(ValueError, match="duration_ms"):
        AuditRecorder().record(session, make_request(duration_ms=-1))


class _NestedTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FailingSession:
    def __init__(self, error):
        self.error = error
        self.added = None
        self.queries = 0

    def scalar(self, statement):
        self.queries += 1
        return None if self.queries == 1 else "existing-event"

    def begin_nested(self):
        return _NestedTransaction()

    def add(self, event):
        self.added = event

    def flush(self):
        raise self.error


def test_named_idempotency_conflict_returns_existing_record():
    error = IntegrityError(
        "insert",
        {},
        Exception("uq_audit_idempotency_key"),
    )
    session = _FailingSession(error)

    result = AuditRecorder().record(session, make_request())

    assert result == "existing-event"
    assert session.queries == 2


def test_record_with_result_reports_existing_after_unique_key_race():
    error = IntegrityError(
        "insert",
        {},
        Exception("uq_audit_idempotency_key"),
    )
    session = _FailingSession(error)

    result = AuditRecorder().record_with_result(session, make_request())

    assert result.event == "existing-event"
    assert result.inserted is False


def test_unrelated_integrity_error_is_not_swallowed():
    error = IntegrityError("insert", {}, Exception("NOT NULL constraint failed"))
    session = _FailingSession(error)

    with pytest.raises(IntegrityError) as raised:
        AuditRecorder().record(session, make_request())

    assert raised.value is error


@pytest.mark.parametrize(
    "field",
    [
        "unit_id",
        "category",
        "source",
        "action",
        "status",
        "risk_level",
        "idempotency_key",
    ],
)
@pytest.mark.parametrize("value", [None, 7, ""])
def test_record_stably_rejects_invalid_required_strings(session, field, value):
    with pytest.raises(ValueError, match=field):
        AuditRecorder().record(session, make_request(**{field: value}))


@pytest.mark.parametrize("value", [None, "2026-08-03T00:00:00Z", 7])
def test_record_stably_rejects_non_datetime_occurred_at(session, value):
    with pytest.raises(ValueError, match="occurred_at"):
        AuditRecorder().record(session, make_request(occurred_at=value))


def test_unrelated_integrity_error_rolls_back_savepoint_not_outer_transaction():
    from sqlalchemy import event

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    raised_once = False

    def fail_first_insert(mapper, connection, target):
        nonlocal raised_once
        if not raised_once:
            raised_once = True
            raise IntegrityError(
                "insert", {}, Exception("unrelated check constraint")
            )

    event.listen(AuditEvent, "before_insert", fail_first_insert)
    try:
        with Session(engine) as real_session:
            with real_session.begin():
                with pytest.raises(
                    IntegrityError, match="unrelated check constraint"
                ):
                    AuditRecorder().record(real_session, make_request())

                recovered = AuditRecorder().record(
                    real_session,
                    make_request(idempotency_key="audit-key-after-error"),
                )
                recovered_id = recovered.id

        with Session(engine) as verification_session:
            assert verification_session.get(AuditEvent, recovered_id) is not None
            assert verification_session.scalar(
                select(func.count()).select_from(AuditEvent)
            ) == 1
    finally:
        event.remove(AuditEvent, "before_insert", fail_first_insert)


@pytest.mark.parametrize("value", ["12", 1.5, True])
def test_record_rejects_non_integer_duration(session, value):
    with pytest.raises(ValueError, match="duration_ms"):
        AuditRecorder().record(session, make_request(duration_ms=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", None),
        ("metadata", None),
        ("allowed_metadata_keys", "safe"),
        ("allowed_metadata_keys", b"safe"),
        ("allowed_metadata_keys", {1}),
    ],
)
def test_record_stably_rejects_invalid_redaction_input_types(
    session, field, value
):
    with pytest.raises(ValueError, match=field):
        AuditRecorder().record(session, make_request(**{field: value}))
