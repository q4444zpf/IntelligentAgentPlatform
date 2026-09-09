import os
import subprocess
import sys

import pytest
from app.skills.project_startup import (
    ProjectSkillStartupError,
    validate_project_skills_startup,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

MIGRATION_DATABASE = "iap_project_skill_activation_migration_20260909_a"
ALEMBIC = (
    sys.executable,
    "-m",
    "alembic",
    "-c",
    "backend/alembic.ini",
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires dedicated PostgreSQL migration database",
)


def _run_migration(*arguments: str) -> None:
    environment = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run((*ALEMBIC, *arguments), check=True, env=environment)


@pytest.fixture(autouse=True)
def empty_migration_database():
    configured_url = os.getenv("TEST_DATABASE_URL")
    if not configured_url:
        pytest.skip("requires dedicated PostgreSQL migration database")
    rejection = (
        "requires exact dedicated PostgreSQL migration database "
        "without query parameters"
    )
    try:
        url = make_url(configured_url)
    except (ArgumentError, ValueError):
        raise ValueError(rejection) from None
    if (
        url.get_backend_name() != "postgresql"
        or url.database != MIGRATION_DATABASE
        or url.query
    ):
        raise ValueError(rejection)

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            if (
                connection.scalar(text("SELECT current_database()"))
                != MIGRATION_DATABASE
            ):
                raise ValueError(
                    "connected database is not the dedicated migration database"
                )
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        yield
    finally:
        engine.dispose()


def test_requires_current_database_revision_before_project_skills_start():
    _run_migration("upgrade", "20260908_26")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    factory = sessionmaker(bind=engine)
    try:
        with pytest.raises(ProjectSkillStartupError, match="revision mismatch"):
            validate_project_skills_startup(True, factory)

        _run_migration("upgrade", "head")
        validate_project_skills_startup(True, factory)
    finally:
        engine.dispose()
