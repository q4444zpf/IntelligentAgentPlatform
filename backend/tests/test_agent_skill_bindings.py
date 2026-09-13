from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.schemas import AgentCreateRequest, AgentConfig
from app.agents.service import AgentService, AgentValidationError
from app.agents.store import AgentStore
from app.core.request_context import RequestContext
from app.db.base import Base
from app.skills.models import Skill, SkillVersion


def _context():
    return RequestContext(
        user_id="user-1",
        unit_id="unit-1",
        project_id="project-1",
        roles=frozenset({"unit_admin"}),
    )


def _published_skill(session, *, name="forecast"):
    skill_id = str(uuid4())
    version_id = str(uuid4())
    skill = Skill(
        id=skill_id,
        unit_id="unit-1",
        project_id="project-1",
        name=name,
        created_by="user-1",
    )
    version = SkillVersion(
        id=version_id,
        skill_id=skill_id,
        version=1,
        source_revision=1,
        idempotency_key=f"publish-{name}",
        request_digest="a" * 64,
        published_by="user-1",
        published_at=datetime(2026, 9, 13, tzinfo=UTC),
        name=name,
        description="Forecast instructions",
        display_version="1.0",
        content="Use the published forecast method.",
        files=[],
        package_digest="b" * 64,
        object_key=f"unit-1/project-1/{skill_id}/archive.zip",
        archive_sha256="c" * 64,
        size_bytes=1,
    )
    session.add_all((skill, version))
    session.flush()
    skill.published_version_id = version_id
    return skill_id, version_id


def test_agent_create_resolves_skill_names_to_current_published_version(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    service = AgentService(
        AgentStore(sessions), workspace_root=tmp_path / "agent-workspaces"
    )
    context = _context()

    with sessions.begin() as session:
        skill_id, version_id = _published_skill(session)

    with sessions() as session:
        created = service.create(
            AgentCreateRequest(
                id="forecast-agent",
                name="Forecast agent",
                skill_names=["forecast"],
                skill_bindings=[{
                    "skill_id": str(uuid4()),
                    "version_id": str(uuid4()),
                    "name": "forged",
                }],
            ),
            context=context,
            session=session,
        )

    assert created.skill_names == ["forecast"]
    assert [binding.model_dump() for binding in created.skill_bindings] == [{
        "skill_id": skill_id,
        "version_id": version_id,
        "name": "forecast",
    }]


def test_migrate_legacy_skill_bindings_resolves_current_published_version(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(sessions)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    context = _context()

    with sessions.begin() as session:
        skill_id, version_id = _published_skill(session)
    workspace = service.workspace_root / "legacy-agent"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# Legacy\n", encoding="utf-8")
    store.create(
        "legacy-agent",
        AgentConfig(name="Legacy", skill_names=["forecast"]).model_dump(),
        str(workspace),
        availability_scope="project",
        unit_id=context.unit_id,
        project_id=context.project_id,
        allowed_project_ids=[],
    )

    with sessions() as session:
        migrated = service.migrate_legacy_skill_bindings(
            "legacy-agent", context=context, session=session
        )

    assert [binding.model_dump() for binding in migrated.skill_bindings] == [{
        "skill_id": skill_id,
        "version_id": version_id,
        "name": "forecast",
    }]


def test_agent_create_rejects_skill_published_in_another_project_scope(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    service = AgentService(
        AgentStore(sessions), workspace_root=tmp_path / "agent-workspaces"
    )
    with sessions.begin() as session:
        _published_skill(session)
    other_context = _context().model_copy(update={"project_id": "project-2"})

    with sessions() as session:
        with pytest.raises(AgentValidationError, match="Published skills are unavailable"):
            service.create(
                AgentCreateRequest(
                    id="out-of-scope-agent",
                    name="Out of scope",
                    skill_names=["forecast"],
                ),
                context=other_context,
                session=session,
            )
