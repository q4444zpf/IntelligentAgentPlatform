import os
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db.base import Base
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import Menu, Unit


def test_alembic_upgraded_sqlite_seeds_control_catalogue(tmp_path):
    root = Path(__file__).resolve().parents[3]
    url = f"sqlite:///{(tmp_path / 'catalogue.db').as_posix()}"
    engine = create_engine(url)
    versions = root / "backend/alembic/versions"

    def load(name):
        spec = importlib.util.spec_from_file_location(name, versions / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # Earlier revisions require PostgreSQL. Preserve the actual historical menu
    # CHECK in the SQLite starting schema, then run the real control migrations.
    metadata = sa.MetaData()
    for table in Base.metadata.tables.values():
        if not table.name.startswith("control_"):
            table.to_metadata(metadata)
    menus = metadata.tables["menus"]
    menus.constraints.remove(next(item for item in menus.constraints if item.name == "ck_menu_catalogue"))
    menus.append_constraint(sa.CheckConstraint(load("20260804_09_identity_authorization_foundation.py").MENU_CATALOGUE_CHECK, name="ck_menu_catalogue"))
    team_versions = metadata.tables["collaboration_team_versions"]
    team_versions.constraints.remove(next(item for item in team_versions.constraints if item.name == "ck_collaboration_team_versions_failure_strategy"))
    metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            load("20260913_28_control_commands.py").upgrade()
        connection.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO alembic_version VALUES ('20260914_30')"))
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(root / "backend/alembic.ini"), "upgrade", "head"],
        cwd=root, env=os.environ | {"DATABASE_URL": url}, capture_output=True, text=True,
    )
    assert migrated.returncode == 0, migrated.stderr
    try:
        with Session(engine) as session:
            session.add(Unit(id="catalogue-unit", code="catalogue-unit", name="Catalogue test", status="active"))
            session.flush()
            seed_builtin_catalogue(session, "catalogue-unit")
            session.commit()
            control = session.scalar(select(Menu).where(Menu.route_key == "control"))
            assert (control.visibility_target, control.requires_current_project) == ("current_project", True)
            seed_builtin_catalogue(session, "catalogue-unit")
            session.commit()
            assert len(session.scalars(select(Menu).where(Menu.route_key == "control")).all()) == 1
            for field, invalid in (("visibility_target", None), ("route_key", None), ("visibility_target", "unit"), ("requires_current_project", False)):
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        setattr(control, field, invalid)
                        session.flush()
    finally:
        engine.dispose()
