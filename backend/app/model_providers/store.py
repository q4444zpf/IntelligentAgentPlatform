import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


EMPTY_STATE = {"providers": {}, "custom_providers": {}, "active_model": {}}


class SqliteStore:
    """SQLite persistence adapter for model-provider runtime configuration.

    Built-in definitions remain in ``registry.py``. Only user configuration,
    custom providers, model overrides and the active model are persisted.
    """

    def __init__(self, path: str | Path | None = None):
        data_dir = Path(__file__).resolve().parents[2] / "data"
        default_path = data_dir / "model-providers.db"
        self.path = Path(path or os.getenv("MODEL_PROVIDER_DATABASE", default_path))
        self.legacy_path = Path(os.getenv("MODEL_PROVIDER_DATA_FILE", data_dir / "model-providers.json"))
        self.lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._migrate_legacy_json()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_configs (
                    provider_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS custom_providers (
                    provider_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS platform_settings (
                    setting_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _migrate_legacy_json(self) -> None:
        if not self.legacy_path.exists() or self.legacy_path == self.path:
            return
        with self.lock, self._connect() as connection:
            has_data = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provider_configs) OR "
                "EXISTS(SELECT 1 FROM custom_providers) OR "
                "EXISTS(SELECT 1 FROM platform_settings)"
            ).fetchone()[0]
            if has_data:
                return
            try:
                with self.legacy_path.open("r", encoding="utf-8") as file:
                    legacy = json.load(file)
                self._save_with_connection(connection, legacy)
                self.legacy_path.rename(self.legacy_path.with_suffix(".json.migrated"))
            except (OSError, json.JSONDecodeError, sqlite3.Error):
                # Keep the original file untouched so migration can be retried.
                return

    @staticmethod
    def _decode_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
        return {row["provider_id"]: json.loads(row["config_json"]) for row in rows}

    def load(self) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            providers = self._decode_rows(connection.execute("SELECT provider_id, config_json FROM provider_configs").fetchall())
            custom = self._decode_rows(connection.execute("SELECT provider_id, config_json FROM custom_providers").fetchall())
            active_row = connection.execute("SELECT value_json FROM platform_settings WHERE setting_key = 'active_model'").fetchone()
            return {
                "providers": providers,
                "custom_providers": custom,
                "active_model": json.loads(active_row["value_json"]) if active_row else {},
            }

    @staticmethod
    def _upsert_many(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
        connection.execute(f"DELETE FROM {table}")
        connection.executemany(
            f"INSERT INTO {table} (provider_id, config_json) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()],
        )

    def _save_with_connection(self, connection: sqlite3.Connection, data: dict[str, Any]) -> None:
        self._upsert_many(connection, "provider_configs", data.get("providers", {}))
        self._upsert_many(connection, "custom_providers", data.get("custom_providers", {}))
        connection.execute(
            """
            INSERT INTO platform_settings (setting_key, value_json)
            VALUES ('active_model', ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(data.get("active_model", {}), ensure_ascii=False),),
        )

    def save(self, data: dict[str, Any]) -> None:
        with self.lock, self._connect() as connection:
            self._save_with_connection(connection, data)

