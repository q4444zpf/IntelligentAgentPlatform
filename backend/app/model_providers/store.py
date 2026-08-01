from copy import deepcopy
from typing import Any

from sqlalchemy import update, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import CustomProviderRecord, PlatformSettingRecord, ProviderConfigRecord


EMPTY_STATE = {"providers": {}, "custom_providers": {}, "active_model": {}}


class ConcurrentProviderUpdateError(ValueError):
    pass


class ProviderState(dict[str, Any]):
    def __init__(self, data: dict[str, Any], versions: dict[str, dict[str, int] | int]):
        super().__init__(data)
        self.original = deepcopy(data)
        self.versions = versions


class ProviderStore:
    def __init__(self, session_factory: sessionmaker[Session] | None = None):
        self.session_factory = session_factory or SessionFactory

    def load(self) -> dict[str, Any]:
        with self.session_factory() as session:
            provider_rows = list(session.scalars(select(ProviderConfigRecord)))
            custom_rows = list(session.scalars(select(CustomProviderRecord)))
            providers = {row.provider_id: row.config for row in provider_rows}
            custom = {row.provider_id: row.config for row in custom_rows}
            active = session.get(PlatformSettingRecord, "active_model")
            return ProviderState({
                "providers": providers,
                "custom_providers": custom,
                "active_model": active.value if active else {},
            }, {
                "providers": {row.provider_id: row.version for row in provider_rows},
                "custom_providers": {row.provider_id: row.version for row in custom_rows},
                "active_model": active.version if active else 0,
            })

    def save(self, data: dict[str, Any]) -> None:
        original = data.original if isinstance(data, ProviderState) else EMPTY_STATE
        versions = data.versions if isinstance(data, ProviderState) else {"providers": {}, "custom_providers": {}, "active_model": 0}
        try:
            with self.session_factory.begin() as session:
                self._save_bucket(session, ProviderConfigRecord, "providers", data, original, versions)
                self._save_bucket(session, CustomProviderRecord, "custom_providers", data, original, versions)
                active_value = data.get("active_model", {})
                if active_value != original.get("active_model", {}):
                    expected = int(versions["active_model"])
                    if expected:
                        result = session.execute(
                            update(PlatformSettingRecord)
                            .where(PlatformSettingRecord.setting_key == "active_model", PlatformSettingRecord.version == expected)
                            .values(value=active_value, version=expected + 1)
                        )
                        if result.rowcount != 1:
                            raise ConcurrentProviderUpdateError("Provider configuration changed concurrently; retry the request")
                    else:
                        session.add(PlatformSettingRecord(setting_key="active_model", value=active_value))
        except IntegrityError as exc:
            raise ConcurrentProviderUpdateError("Provider configuration changed concurrently; retry the request") from exc

    @staticmethod
    def _save_bucket(session: Session, model: type, bucket: str, data: dict[str, Any], original: dict[str, Any], versions: dict[str, Any]) -> None:
        old_values = original.get(bucket, {})
        for key, value in data.get(bucket, {}).items():
            if key in old_values and value == old_values[key]:
                continue
            expected = versions[bucket].get(key, 0)
            if expected:
                result = session.execute(
                    update(model)
                    .where(model.provider_id == key, model.version == expected)
                    .values(config=value, version=expected + 1)
                )
                if result.rowcount != 1:
                    raise ConcurrentProviderUpdateError("Provider configuration changed concurrently; retry the request")
            else:
                session.add(model(provider_id=key, config=value))
