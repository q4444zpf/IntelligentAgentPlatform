from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.platform_models import McpClientRecord, McpHealthCheckRecord, McpOperationRecord
from app.db.platform_models import RegisteredToolRecord
from app.mcp.health_service import McpHealthService


def _service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'health.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory.begin() as session:
        session.add(McpClientRecord(client_key="water", client_id="water", unit_id="unit-1", config={"name": "Water", "enabled": True, "transport": "streamable_http", "url": "https://example.test/mcp", "headers": {}, "credential_id": None}, tool_records=[]))
    return McpHealthService(factory, lease_seconds=60, failure_threshold=2), factory


def test_health_check_is_due_after_five_minutes(tmp_path):
    service, factory = _service(tmp_path)
    now = datetime.now(UTC)
    with factory() as session:
        assert service.is_due(session, "water", now=now) is True
        session.get(McpClientRecord, "water").last_checked_at = now - timedelta(seconds=299)
        assert service.is_due(session, "water", now=now) is False
        session.get(McpClientRecord, "water").last_checked_at = now - timedelta(seconds=300)
        assert service.is_due(session, "water", now=now) is True


def test_database_lease_allows_only_one_health_worker(tmp_path):
    service, factory = _service(tmp_path)
    now = datetime.now(UTC)
    with factory.begin() as session:
        assert service.acquire_lease(session, "water", now=now) is True
    with factory.begin() as session:
        assert service.acquire_lease(session, "water", now=now) is False
        assert service.release_lease(session, "water", now=now) is True
    with factory.begin() as session:
        assert service.acquire_lease(session, "water", now=now) is True


def test_success_and_consecutive_failures_update_state_and_history(tmp_path):
    service, factory = _service(tmp_path)
    now = datetime.now(UTC)
    with factory.begin() as session:
        service.record_result(session, "water", ok=True, phase="tools/list", latency_ms=12, now=now)
    with factory() as session:
        row = session.get(McpClientRecord, "water")
        assert row.health_status == "healthy"
        assert row.failure_count == 0
        assert session.scalar(select(McpHealthCheckRecord).where(McpHealthCheckRecord.client_id == "water")) is not None
    with factory.begin() as session:
        service.record_result(session, "water", ok=False, phase="initialize", error_code="TIMEOUT", error_message="remote unavailable", now=now + timedelta(seconds=1))
        service.record_result(session, "water", ok=False, phase="initialize", error_code="TIMEOUT", error_message="remote unavailable", now=now + timedelta(seconds=2))
    with factory() as session:
        row = session.get(McpClientRecord, "water")
        assert row.health_status == "offline"
        assert row.failure_count == 2
        assert session.scalar(select(McpHealthCheckRecord).where(McpHealthCheckRecord.status == "offline")) is not None


def test_manual_operation_is_persisted_and_can_complete(tmp_path):
    service, factory = _service(tmp_path)
    with factory.begin() as session:
        operation = service.start_operation(session, "water", "manual_test")
        assert operation.status == "queued"
        service.update_operation(session, operation.id, status="succeeded", phase="tools/list", result={"tool_count": 2})
    with factory() as session:
        saved = session.get(McpOperationRecord, operation.id)
        assert saved.status == "succeeded"
        assert saved.result == {"tool_count": 2}
        assert saved.completed_at is not None


def test_offline_result_unpublishes_source_tools(tmp_path):
    service, factory = _service(tmp_path)
    with factory.begin() as session:
        session.add(RegisteredToolRecord(tool_id="mcp.water.one", version="1", name="one", description="", source="mcp", risk_level="medium", input_schema={"type": "object"}, output_schema={}, source_resource_id="water", source_capability_id="one", source_available=True, published=True, enabled=True))
    with factory.begin() as session:
        service.record_result(session, "water", ok=False, phase="initialize", error_code="TIMEOUT", error_message="offline")
        service.record_result(session, "water", ok=False, phase="initialize", error_code="TIMEOUT", error_message="offline")
        service.mark_source_unavailable(session, "water")
    with factory() as session:
        tool = session.get(RegisteredToolRecord, "mcp.water.one")
        assert tool.source_available is False
        assert tool.published is False
