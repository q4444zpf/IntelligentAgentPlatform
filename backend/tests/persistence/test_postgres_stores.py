from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.store import AgentStore
from app.db.base import Base
from app.mcp.store import McpStore
from app.model_providers.store import ProviderStore
from app.model_providers.store import ConcurrentProviderUpdateError
import pytest


def build_factory(database: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def test_provider_store_persists_state_in_shared_database(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    store = ProviderStore(factory)
    state = {
        "providers": {"deepseek": {"api_key": "secret"}},
        "custom_providers": {"water": {"definition": {"id": "water"}}},
        "active_model": {"provider_id": "deepseek", "model": "deepseek-chat"},
    }

    store.save(state)

    assert ProviderStore(factory).load() == state


def test_provider_store_does_not_delete_rows_missing_from_stale_snapshot(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    first = ProviderStore(factory)
    stale = first.load()

    first.save({"providers": {"new": {"api_key": "new"}}, "custom_providers": {}, "active_model": {}})
    ProviderStore(factory).save({**stale, "providers": {"old": {"api_key": "old"}}})

    assert ProviderStore(factory).load()["providers"] == {
        "new": {"api_key": "new"},
        "old": {"api_key": "old"},
    }


def test_provider_store_rejects_stale_update_to_same_provider(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    store = ProviderStore(factory)
    store.save({"providers": {"shared": {"models": []}}, "custom_providers": {}, "active_model": {}})
    first = store.load()
    stale = store.load()
    first["providers"]["shared"] = {"models": ["first"]}
    stale["providers"]["shared"] = {"models": ["stale"]}

    store.save(first)
    with pytest.raises(ConcurrentProviderUpdateError):
        store.save(stale)

    assert store.load()["providers"]["shared"] == {"models": ["first"]}


def test_provider_save_does_not_revert_concurrently_changed_active_model(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    store = ProviderStore(factory)
    store.save({"providers": {}, "custom_providers": {}, "active_model": {"model": "old"}})
    provider_change = store.load()
    active_change = store.load()
    active_change["active_model"] = {"model": "new"}
    store.save(active_change)
    provider_change["providers"]["deepseek"] = {"api_key": "configured"}

    store.save(provider_change)

    assert store.load()["active_model"] == {"model": "new"}


def test_agent_store_preserves_crud_contract(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    store = AgentStore(factory)

    created = store.create("flood", {"name": "洪水智能体", "enabled": True}, "D:/work/flood")
    assert created["id"] == "flood"
    assert store.set_pinned("flood", True)["pinned"] is True
    assert store.update("flood", {"name": "洪水预报智能体", "enabled": False})["enabled"] is False
    assert AgentStore(factory).get("flood")["name"] == "洪水预报智能体"
    assert store.delete("flood")["id"] == "flood"


def test_mcp_store_preserves_tools_and_whitelist(tmp_path):
    factory = build_factory(tmp_path / "platform.db")
    store = McpStore(factory)
    store.create("water", {"name": "水情 MCP", "headers": {}, "enabled": True})

    store.update_tools("water", [{"name": "query_level"}], "2026-08-01T00:00:00+00:00")
    store.update_whitelist("water", ["query_level"])

    record = McpStore(factory).get("water")
    assert record["tool_records"] == [{"name": "query_level"}]
    assert record["tools"] == ["query_level"]
