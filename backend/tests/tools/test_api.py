import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.tools.router import create_router
from app.tools.service import ToolService
from app.tools.store import ToolStore


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tools-api.db'}")
    Base.metadata.create_all(engine)
    service = ToolService(
        ToolStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    )
    app = FastAPI()
    app.include_router(create_router(service), prefix="/api/tools")
    with TestClient(app) as test_client:
        yield test_client


def test_initializes_two_builtin_tools(client):
    response = client.get("/api/tools")

    assert response.status_code == 200
    tools = response.json()
    assert [item["tool_id"] for item in tools] == [
        "system.get_current_time",
        "system.get_runtime_context",
    ]
    assert all(item["source"] == "builtin" for item in tools)
    assert all(item["version"] == "1.0.0" for item in tools)
    assert all(item["published"] is True for item in tools)


def test_reads_toggles_and_returns_not_found(client):
    read = client.get("/api/tools/system.get_current_time")
    assert read.status_code == 200
    assert read.json()["enabled"] is True

    toggled = client.patch("/api/tools/system.get_current_time/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    missing = client.get("/api/tools/system.missing")
    assert missing.status_code == 404


def test_invalid_tool_id_returns_unprocessable_entity(client):
    response = client.get("/api/tools/invalid$id")
    assert response.status_code == 422
