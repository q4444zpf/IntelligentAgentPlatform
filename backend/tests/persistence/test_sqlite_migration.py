import json
import sqlite3
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.store import AgentStore
from app.db.base import Base
from app.mcp.store import McpStore
from app.migrations.sqlite_to_postgres import migrate_sqlite_databases
from app.model_providers.store import ProviderStore


def build_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'target.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def test_imports_all_legacy_sqlite_domains_once(tmp_path):
    providers = tmp_path / "providers.db"
    with sqlite3.connect(providers) as connection:
        connection.executescript("""
        CREATE TABLE provider_configs (provider_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT);
        CREATE TABLE custom_providers (provider_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, created_at TEXT, updated_at TEXT);
        CREATE TABLE platform_settings (setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT);
        """)
        connection.execute("INSERT INTO provider_configs VALUES (?, ?, CURRENT_TIMESTAMP)", ("deepseek", json.dumps({"api_key": "legacy"})))
        connection.execute("INSERT INTO platform_settings VALUES (?, ?, CURRENT_TIMESTAMP)", ("active_model", json.dumps({"provider_id": "deepseek", "model": "deepseek-chat"})))

    agents = tmp_path / "agents.db"
    with sqlite3.connect(agents) as connection:
        connection.execute("CREATE TABLE agents (agent_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, workspace_dir TEXT NOT NULL, pinned INTEGER, created_at TEXT, updated_at TEXT)")
        connection.execute("INSERT INTO agents VALUES (?, ?, ?, 1, ?, ?)", ("flood", json.dumps({"name": "洪水智能体", "enabled": True}), "D:/work/flood", "2025-01-02 03:04:05", "2025-02-03 04:05:06"))

    mcp = tmp_path / "mcp.db"
    with sqlite3.connect(mcp) as connection:
        connection.execute("CREATE TABLE mcp_clients (client_key TEXT PRIMARY KEY, config_json TEXT NOT NULL, tools_json TEXT NOT NULL, whitelist_json TEXT, last_synced_at TEXT, created_at TEXT, updated_at TEXT)")
        connection.execute("INSERT INTO mcp_clients VALUES (?, ?, ?, NULL, NULL, ?, ?)", ("water", json.dumps({"name": "水情 MCP", "headers": {}, "enabled": True}), "[]", "2025-03-04 05:06:07", "2025-04-05 06:07:08"))

    factory = build_factory(tmp_path)
    ProviderStore(factory).save({
        "providers": {"existing": {"api_key": "postgres"}},
        "custom_providers": {},
        "active_model": {},
    })
    AgentStore(factory).create("existing", {"name": "现有智能体", "enabled": True}, "D:/work/existing")
    McpStore(factory).create("existing", {"name": "现有 MCP", "headers": {}, "enabled": True})
    result = migrate_sqlite_databases(factory, providers, agents, mcp)

    assert result == {"model_providers": 2, "agents": 1, "mcp_clients": 1}
    assert ProviderStore(factory).load()["providers"]["deepseek"]["api_key"] == "legacy"
    assert ProviderStore(factory).load()["providers"]["existing"]["api_key"] == "postgres"
    migrated_agent = AgentStore(factory).get("flood")
    assert migrated_agent["pinned"] is True
    assert migrated_agent["created_at"] == datetime(2025, 1, 2, 3, 4, 5)
    assert migrated_agent["updated_at"] == datetime(2025, 2, 3, 4, 5, 6)
    assert migrated_agent["workspace_dir"] == str(tmp_path / "agent-workspaces" / "flood")
    assert (tmp_path / "agent-workspaces" / "flood" / "AGENTS.md").is_file()
    migrated_mcp = McpStore(factory).get("water")
    assert migrated_mcp["name"] == "水情 MCP"
    assert migrated_mcp["created_at"] == datetime(2025, 3, 4, 5, 6, 7)
    assert migrated_mcp["updated_at"] == datetime(2025, 4, 5, 6, 7, 8)

    second = migrate_sqlite_databases(factory, providers, agents, mcp)
    assert second == {"model_providers": 0, "agents": 0, "mcp_clients": 0}
