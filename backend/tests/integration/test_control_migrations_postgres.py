import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from backend.tests.integration.postgres_migration_helpers import isolated_migration_database


ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT_SQL = "SELECT id, failure_strategy FROM collaboration_team_versions WHERE failure_strategy NOT IN ('fail_fast','continue_then_synthesize')"
pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL intentionally absent; dedicated PostgreSQL required",
)


@pytest.fixture
def database():
    with isolated_migration_database() as url:
        engine = sa.create_engine(url)

        def migrate(revision, *, direction="upgrade", check=True):
            return subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), direction, revision],
                cwd=ROOT, env=os.environ | {"DATABASE_URL": url},
                capture_output=True, text=True, check=check,
            )

        try:
            yield engine, migrate
        finally:
            engine.dispose()


def table(connection, name):
    return sa.Table(name, sa.MetaData(), autoload_with=connection)


def seed_commands(engine):
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        # Nullable legacy artifact ownership is allowed; no fabricated Team provenance is needed here.
        artifacts = table(connection, "artifacts")
        artifact_values = dict(id="simulation", unit_id="unit", project_id="project", owner_id="requester",
            scope="project", filename="simulation.json", content_type="application/json", size_bytes=2,
            sha256="a" * 64, object_key="migration/simulation.json", status="active")
        connection.execute(artifacts.insert().values(**artifact_values))
        connection.execute(table(connection, "control_policies").insert().values(
            id="policy", unit_id="unit", project_id="project", owner_id="admin", asset_type="gate",
            command_type="set", parameter_schema={"type": "object"}, max_command_lifetime_seconds=300,
            approval_mode="dual", deviation_thresholds={"opening": 2}, enabled=True,
        ))
        commands = table(connection, "control_commands")
        for command_id, status, actual in (
            ("unevaluated", "succeeded", None), ("evaluated", "succeeded", {"opening": 20}),
            ("orphaned", "executing", None),
        ):
            connection.execute(commands.insert().values(
                id=command_id, unit_id="unit", project_id="project", policy_id="policy", requester_id="requester",
                team_version="legacy-version", target={"asset_type": "gate", "id": "gate-1"},
                parameters={"opening": 20}, parameter_digest="b" * 64, simulation_artifact_id="simulation",
                simulation_digest="a" * 64, valid_from=now, valid_until=now + timedelta(minutes=5),
                required_approvals=2, rollback_plan={"opening": 0}, expected_state={"opening": 20},
                actual_state=actual, idempotency_key=command_id, status=status,
            ))
        connection.execute(table(connection, "control_execution_attempts").insert().values(
            id="legacy-attempt", command_id="evaluated", kind="execute", request_key="same-key",
            external_idempotency_key="legacy-external-key", outcome="succeeded", result_summary={"external_id": "physical-1"},
        ))
        connection.execute(table(connection, "control_command_approvals").insert().values(
            id="legacy-approval", command_id="evaluated", approver_id="reviewer", decision="approve",
        ))


def test_control_upgrade_from_project_skill_head_creates_all_constraints(database):
    engine, migrate = database
    migrate("20260908_27")
    migrate("20260915_31")
    inspector = sa.inspect(engine)
    checks = {
        "control_policies": {"ck_control_policy_approval_mode", "ck_control_policy_lifetime"},
        "control_commands": {"ck_control_command_status", "ck_control_command_required_approvals", "ck_control_command_validity"},
        "control_command_approvals": {"ck_control_approval_decision"},
        "control_execution_attempts": {"ck_control_execution_kind", "ck_control_execution_outcome"},
        "control_feedback_records": set(),
    }
    assert set(checks) <= set(inspector.get_table_names())
    for name, expected in checks.items():
        assert {item["name"] for item in inspector.get_check_constraints(name)} == expected
    assert "ck_collaboration_team_versions_failure_strategy" in {
        item["name"] for item in inspector.get_check_constraints("collaboration_team_versions")
    }
    unique = {
        "control_policies": {"uq_control_policy_pair": ["unit_id", "project_id", "asset_type", "command_type"]},
        "control_commands": {"uq_control_command_idempotency": ["unit_id", "project_id", "idempotency_key"]},
        "control_command_approvals": {"uq_control_command_approver": ["command_id", "approver_id"]},
        "control_execution_attempts": {"uq_control_execution_request": ["command_id", "kind", "request_key"], "uq_control_execution_external_key": ["external_idempotency_key"]},
    }
    for name, expected in unique.items():
        assert {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(name)} == expected
    indexes = {
        "control_policies": {"ix_control_policy_scope": ["unit_id", "project_id", "enabled"]},
        "control_commands": {"ix_control_command_scope_status": ["unit_id", "project_id", "status"], "ix_control_commands_status": ["status"]},
        "control_command_approvals": {"ix_control_command_approvals_command_id": ["command_id"]},
        "control_execution_attempts": {"ix_control_execution_attempts_command_id": ["command_id"]},
        "control_feedback_records": {"ix_control_feedback_records_command_id": ["command_id"]},
    }
    for name, expected in indexes.items():
        assert {item["name"]: item["column_names"] for item in inspector.get_indexes(name) if not item.get("duplicates_constraint")} == expected
    for name, column, target, deletion in (
        ("control_commands", "policy_id", "control_policies", "RESTRICT"),
        ("control_commands", "simulation_artifact_id", "artifacts", "RESTRICT"),
        ("control_command_approvals", "command_id", "control_commands", "CASCADE"),
        ("control_execution_attempts", "command_id", "control_commands", "CASCADE"),
        ("control_feedback_records", "command_id", "control_commands", "CASCADE"),
    ):
        foreign_key = next(item for item in inspector.get_foreign_keys(name) if item["constrained_columns"] == [column])
        assert foreign_key["name"] == f"{name}_{column}_fkey"
        assert foreign_key["referred_table"] == target
        assert foreign_key["referred_columns"] == ["id"]
        assert foreign_key["options"]["ondelete"] == deletion
    columns = {item["name"]: item for item in inspector.get_columns("control_commands")}
    assert "team_version" not in columns and "team_version_id" in columns
    assert all(not columns[name]["nullable"] for name in ("policy_digest", "policy_snapshot", "deviation_thresholds_snapshot"))


def test_control_upgrade_from_skill_availability_head_reaches_single_head(database):
    engine, migrate = database
    migrate("20260914_29")
    migrate("20260915_31")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all() == ["20260915_31"]


def test_alembic_upgraded_postgres_seeds_control_catalogue(database):
    from sqlalchemy.orm import Session
    from app.identity.catalogue import seed_builtin_catalogue
    from app.identity.models import Menu, Unit

    engine, migrate = database
    migrate("20260914_30")
    migrate("20260915_31")
    with Session(engine) as session:
        session.add(Unit(id="catalogue-unit", code="catalogue-unit", name="Catalogue test", status="active"))
        session.flush()
        seed_builtin_catalogue(session, "catalogue-unit")
        session.commit()
        control = session.scalar(sa.select(Menu).where(Menu.route_key == "control"))
        assert (control.visibility_target, control.requires_current_project) == ("current_project", True)
        seed_builtin_catalogue(session, "catalogue-unit")
        session.commit()
        assert len(session.scalars(sa.select(Menu).where(Menu.route_key == "control")).all()) == 1
        for field, invalid in (("visibility_target", None), ("route_key", None), ("visibility_target", "unit"), ("requires_current_project", False)):
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    setattr(control, field, invalid)
                    session.flush()
    refused = migrate("20260914_30", direction="downgrade", check=False)
    assert refused.returncode != 0 and "control menu exists" in refused.stderr
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20260915_31"
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM menus WHERE route_key='control'")) == 1


def test_migrated_invalid_threshold_snapshot_cannot_reach_executor(database):
    from sqlalchemy.orm import Session
    from app.control.errors import ControlValidationError
    from app.control.models import ControlCommand
    from app.control.service import ControlService
    from backend.tests.control.test_service import FakeExecutor
    from backend.tests.control.test_api import _context

    engine, migrate = database
    migrate("20260914_30")
    seed_commands(engine)
    with engine.begin() as connection:
        connection.execute(table(connection, "control_policies").update().values(deviation_thresholds={"opening": "invalid"}))
        connection.execute(sa.text("UPDATE control_commands SET status='approved' WHERE id='unevaluated'"))
    migrate("20260915_31")
    executor = FakeExecutor()
    with Session(engine) as session:
        command = session.get(ControlCommand, "unevaluated")
        assert command.deviation_thresholds_snapshot == {"opening": "invalid"}
        context = _context("control.execute", project_id="project")
        context = context.model_copy(update={
            "unit_id": "unit",
            "authorization_context": context.authorization_context.model_copy(update={"unit_id": "unit"}),
        })
        service = ControlService(session, executors={"gate": executor}, clock=lambda: command.valid_from)
        with pytest.raises(ControlValidationError) as error:
            service.execute(command.id, context, "legacy-execute")
        assert (error.value.status_code, error.value.code) == (422, "control_validation_failed")
        assert command.status == "approved"
        assert executor.calls == []


def test_control_migration_aborts_with_actionable_error_for_invalid_legacy_failure_strategy(database):
    engine, migrate = database
    migrate("20260908_27")
    with engine.begin() as connection:
        connection.execute(table(connection, "collaboration_teams").insert().values(id="team", unit_id="unit", project_id="project", name="legacy"))
        connection.execute(table(connection, "collaboration_team_versions").insert().values(
            id="version", team_id="team", version=1, status="published", definition={"failure_strategy": "ignore"},
            definition_digest="d" * 64, tool_ids=[], skill_names=[], knowledge_source_ids=[],
            max_steps=2, max_parallel_members=1, timeout_seconds=60, failure_strategy="ignore",
        ))
    failed = migrate("20260915_31", check=False)
    assert failed.returncode != 0
    assert "1 invalid" in failed.stderr
    assert PREFLIGHT_SQL in failed.stderr
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT failure_strategy, definition_digest FROM collaboration_team_versions WHERE id='version'")).one()
        assert tuple(row) == ("ignore", "d" * 64)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20260908_27"
    assert "control_commands" not in sa.inspect(engine).get_table_names()


def test_control_execution_hardening_backfills_and_downgrades_without_data_loss(database):
    engine, migrate = database
    migrate("20260914_30")
    seed_commands(engine)
    with engine.connect() as connection:
        originals = {name: connection.execute(sa.select(table(connection, name))).mappings().all() for name in (
            "control_commands", "control_command_approvals", "control_execution_attempts",
        )}
    migrate("20260915_31")
    with engine.connect() as connection:
        rows = {row["id"]: row for row in connection.execute(sa.select(table(connection, "control_commands"))).mappings()}
        assert rows["unevaluated"]["status"] == "awaiting_feedback"
        assert rows["evaluated"]["status"] == "succeeded"
        assert rows["evaluated"]["feedback_observed_at"] is not None
        assert rows["orphaned"]["status"] == "unknown"
        snapshot = rows["evaluated"]["policy_snapshot"]
        assert snapshot["approval_mode"] == "dual" and snapshot["deviation_thresholds"] == {"opening": 2}
        assert hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest() == rows["evaluated"]["policy_digest"]
        attempt = connection.execute(sa.select(table(connection, "control_execution_attempts"))).mappings().one()
        assert attempt["completed_at"] == attempt["created_at"] and attempt["external_id"] == "physical-1"
        assert connection.scalar(sa.text("SELECT revision FROM control_policies")) == 1
    refused = migrate("20260914_30", direction="downgrade", check=False)
    assert refused.returncode != 0 and "await feedback" in refused.stderr
    # Resolve the new-only state before downgrade; never silently discard feedback semantics.
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE control_commands SET status='unknown' WHERE id='unevaluated'"))
    migrate("20260914_30", direction="downgrade")
    with engine.connect() as connection:
        for name, before in originals.items():
            expected = [dict(row) for row in before]
            if name == "control_commands":
                for row in expected:
                    if row["id"] in {"orphaned", "unevaluated"}:
                        row["status"] = "unknown"
            after = connection.execute(sa.select(table(connection, name))).mappings().all()
            assert sorted(after, key=lambda row: row["id"]) == sorted(expected, key=lambda row: row["id"])
    assert "control_feedback_records" not in sa.inspect(engine).get_table_names()
    migrate("20260915_31")


def test_postgres_enforces_kind_scoped_attempt_uniqueness_and_closed_states(database):
    engine, migrate = database
    migrate("20260914_30")
    seed_commands(engine)
    migrate("20260915_31")
    with engine.begin() as connection:
        attempts = table(connection, "control_execution_attempts")
        connection.execute(attempts.insert().values(id="rollback", command_id="evaluated", kind="rollback", request_key="same-key", external_idempotency_key="rollback-key", outcome="pending"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(attempts.insert().values(id="duplicate", command_id="evaluated", kind="execute", request_key="same-key", external_idempotency_key="duplicate-key", outcome="pending"))
    for name, column in (("control_commands", "status"), ("control_execution_attempts", "outcome"), ("control_execution_attempts", "kind")):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(sa.text(f"UPDATE {name} SET {column}='unsupported'"))
