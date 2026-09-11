from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session


class ProjectSkillStartupError(RuntimeError):
    pass


def load_code_migration_heads(config_path: Path | None = None) -> frozenset[str]:
    path = config_path or Path(__file__).resolve().parents[2] / "alembic.ini"
    try:
        heads = frozenset(ScriptDirectory.from_config(Config(str(path))).get_heads())
    except Exception:  # noqa: BLE001 - startup boundary replaces graph failures
        heads = frozenset()
    if not heads:
        raise ProjectSkillStartupError(
            "project skill migration graph is unavailable"
        ) from None
    return heads


def _load_database_migration_heads(
    session_factory: Callable[[], Session],
) -> frozenset[str] | None:
    try:
        with session_factory() as session:
            return frozenset(
                session.scalars(text("SELECT version_num FROM alembic_version"))
            )
    except Exception:  # noqa: BLE001 - startup boundary scrubs infrastructure errors
        return None


def validate_project_skills_startup(
    enabled: bool,
    session_factory: Callable[[], Session],
) -> None:
    if not enabled:
        return
    expected = load_code_migration_heads()
    actual = _load_database_migration_heads(session_factory)
    if actual is None:
        raise ProjectSkillStartupError(
            "project skill database revision is unavailable"
        ) from None
    if actual != expected:
        raise ProjectSkillStartupError(
            f"project skill database revision mismatch: "
            f"expected={sorted(expected)!r}, actual={sorted(actual)!r}"
        )
