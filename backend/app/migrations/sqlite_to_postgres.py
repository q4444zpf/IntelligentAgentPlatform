from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import (
    CustomProviderRecord,
    ManagedAgentRecord,
    McpClientRecord,
    PlatformSettingRecord,
    ProviderConfigRecord,
)


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?)",
        (name,),
    ).fetchone()[0] == 1


def _json_rows(connection: sqlite3.Connection, table: str, key: str) -> dict[str, Any]:
    if not _has_table(connection, table):
        return {}
    return {
        row[key]: json.loads(row["config_json"])
        for row in connection.execute(f"SELECT {key}, config_json FROM {table}")
    }


def _legacy_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _insert_once(factory: sessionmaker[Session], record: Any) -> bool:
    try:
        with factory.begin() as session:
            session.add(record)
        return True
    except IntegrityError:
        return False


def _import_active_model(factory: sessionmaker[Session], active: dict[str, Any]) -> bool:
    try:
        with factory.begin() as session:
            row = session.get(PlatformSettingRecord, "active_model", with_for_update=True)
            if row:
                if row.value:
                    return False
                row.value = active
            else:
                session.add(PlatformSettingRecord(setting_key="active_model", value=active))
        return True
    except IntegrityError:
        return False


def _import_providers(factory: sessionmaker[Session], path: Path) -> int:
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        providers = _json_rows(connection, "provider_configs", "provider_id")
        custom = _json_rows(connection, "custom_providers", "provider_id")
        active: dict[str, Any] = {}
        if _has_table(connection, "platform_settings"):
            row = connection.execute("SELECT value_json FROM platform_settings WHERE setting_key = 'active_model'").fetchone()
            if row:
                active = json.loads(row["value_json"])
    count = sum(
        _insert_once(factory, ProviderConfigRecord(provider_id=key, config=value))
        for key, value in providers.items()
    )
    count += sum(
        _insert_once(factory, CustomProviderRecord(provider_id=key, config=value))
        for key, value in custom.items()
    )
    if active:
        count += _import_active_model(factory, active)
    return count


def _import_agents(factory: sessionmaker[Session], path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if not _has_table(connection, "agents"):
            return 0
        rows = connection.execute("SELECT agent_id, config_json, workspace_dir, pinned, created_at, updated_at FROM agents").fetchall()
    for row in rows:
        config = json.loads(row["config_json"])
        workspace = path.parent / "agent-workspaces" / row["agent_id"]
        workspace.mkdir(parents=True, exist_ok=True)
        agents_file = workspace / "AGENTS.md"
        if not agents_file.exists():
            name = config.get("name", row["agent_id"])
            prompt = config.get("system_prompt") or config.get("description") or name
            agents_file.write_text(f"# {name}\n\n{prompt}\n", encoding="utf-8")
        record = ManagedAgentRecord(
            agent_id=row["agent_id"],
            config=config,
            workspace_dir=str(workspace),
            pinned=bool(row["pinned"]),
            created_at=_legacy_datetime(row["created_at"]),
            updated_at=_legacy_datetime(row["updated_at"]),
        )
        count += _insert_once(factory, record)
    return count


def _import_mcp(factory: sessionmaker[Session], path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if not _has_table(connection, "mcp_clients"):
            return 0
        rows = connection.execute("SELECT * FROM mcp_clients").fetchall()
    for row in rows:
        whitelist = json.loads(row["whitelist_json"]) if row["whitelist_json"] is not None else None
        record = McpClientRecord(
            client_key=row["client_key"],
            config=json.loads(row["config_json"]),
            tool_records=json.loads(row["tools_json"]),
            whitelist=whitelist,
            last_synced_at=row["last_synced_at"],
            created_at=_legacy_datetime(row["created_at"]),
            updated_at=_legacy_datetime(row["updated_at"]),
        )
        count += _insert_once(factory, record)
    return count


def migrate_sqlite_databases(
    session_factory: sessionmaker[Session],
    provider_database: str | Path,
    agent_database: str | Path,
    mcp_database: str | Path,
) -> dict[str, int]:
    return {
        "model_providers": _import_providers(session_factory, Path(provider_database)),
        "agents": _import_agents(session_factory, Path(agent_database)),
        "mcp_clients": _import_mcp(session_factory, Path(mcp_database)),
    }


def main() -> None:
    data_dir = Path(os.getenv("LEGACY_SQLITE_DATA_DIR", "/data"))
    result = migrate_sqlite_databases(
        SessionFactory,
        os.getenv("LEGACY_MODEL_PROVIDER_DATABASE", data_dir / "model-providers.db"),
        os.getenv("LEGACY_AGENT_DATABASE", data_dir / "agents.db"),
        os.getenv("LEGACY_MCP_DATABASE", data_dir / "mcp.db"),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
