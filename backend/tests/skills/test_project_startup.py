from pathlib import Path

import pytest
from app.skills.project_startup import (
    ProjectSkillStartupError,
    load_code_migration_heads,
    validate_project_skills_startup,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _write_migration_config(tmp_path: Path, revisions: dict[str, str | None]) -> Path:
    script_directory = tmp_path / "alembic"
    versions_directory = script_directory / "versions"
    versions_directory.mkdir(parents=True)
    config_path = tmp_path / "alembic.ini"
    config_path.write_text(
        "[alembic]\nscript_location = %(here)s/alembic\n",
        encoding="ascii",
    )
    for revision, down_revision in revisions.items():
        (versions_directory / f"{revision}.py").write_text(
            "\n".join(
                (
                    f'revision = "{revision}"',
                    f"down_revision = {down_revision!r}",
                    "branch_labels = None",
                    "depends_on = None",
                    "",
                )
            ),
            encoding="ascii",
        )
    return config_path


def _session_factory_with_heads(*heads: str):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        for head in heads:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": head},
            )
    return sessionmaker(bind=engine)


def _complete_exception_chain(error: BaseException) -> list[BaseException]:
    chain = []
    current: BaseException | None = error
    while current is not None:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def test_loads_repository_migration_heads_without_using_current_directory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    heads = load_code_migration_heads()
    assert heads
    assert all(head.isascii() and head.strip() == head for head in heads)


def test_rejects_empty_migration_graph_without_exception_chain(tmp_path):
    config_path = _write_migration_config(tmp_path, {})

    with pytest.raises(
        ProjectSkillStartupError,
        match="^project skill migration graph is unavailable$",
    ) as captured:
        load_code_migration_heads(config_path)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_rejects_broken_migration_graph_without_exception_chain(tmp_path):
    config_path = _write_migration_config(
        tmp_path,
        {"synthetic_head": None},
    )
    (tmp_path / "alembic" / "versions" / "synthetic_head.py").write_text(
        "revision =",
        encoding="ascii",
    )

    with pytest.raises(
        ProjectSkillStartupError,
        match="^project skill migration graph is unavailable$",
    ) as captured:
        load_code_migration_heads(config_path)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_loads_every_head_from_synthetic_migration_graph(tmp_path):
    config_path = _write_migration_config(
        tmp_path,
        {"synthetic_head_a": None, "synthetic_head_b": None},
    )

    assert load_code_migration_heads(config_path) == frozenset(
        {"synthetic_head_a", "synthetic_head_b"}
    )


def test_disabled_gate_does_not_access_database():
    def inaccessible_factory():
        raise AssertionError("database must not be accessed")

    validate_project_skills_startup(False, inaccessible_factory)


def test_accepts_exact_database_revision(monkeypatch):
    monkeypatch.setattr(
        "app.skills.project_startup.load_code_migration_heads",
        lambda: frozenset({"expected_head"}),
    )

    validate_project_skills_startup(
        True,
        _session_factory_with_heads("expected_head"),
    )


@pytest.mark.parametrize(
    ("expected", "actual", "message"),
    [
        (
            {"new_head"},
            {"old_head"},
            (
                "project skill database revision mismatch: "
                "expected=['new_head'], actual=['old_head']"
            ),
        ),
        (
            {"old_head"},
            {"new_head"},
            (
                "project skill database revision mismatch: "
                "expected=['old_head'], actual=['new_head']"
            ),
        ),
        (
            {"expected_b", "expected_a"},
            {"actual_b", "actual_a"},
            (
                "project skill database revision mismatch: "
                "expected=['expected_a', 'expected_b'], "
                "actual=['actual_a', 'actual_b']"
            ),
        ),
    ],
    ids=["database-behind", "database-ahead", "two-database-heads"],
)
def test_rejects_database_revision_mismatch(
    monkeypatch,
    expected,
    actual,
    message,
):
    monkeypatch.setattr(
        "app.skills.project_startup.load_code_migration_heads",
        lambda: frozenset(expected),
    )

    with pytest.raises(ProjectSkillStartupError, match="revision mismatch") as captured:
        validate_project_skills_startup(
            True,
            _session_factory_with_heads(*actual),
        )

    assert str(captured.value) == message


def test_rejects_missing_database_revision_table(monkeypatch):
    monkeypatch.setattr(
        "app.skills.project_startup.load_code_migration_heads",
        lambda: frozenset({"expected_head"}),
    )
    engine = create_engine("sqlite://")

    with pytest.raises(
        ProjectSkillStartupError,
        match="^project skill database revision is unavailable$",
    ) as captured:
        validate_project_skills_startup(True, sessionmaker(bind=engine))

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_replaces_secret_bearing_database_failure_without_exception_chain(monkeypatch):
    monkeypatch.setattr(
        "app.skills.project_startup.load_code_migration_heads",
        lambda: frozenset({"expected_head"}),
    )

    def secret_bearing_factory():
        raise RuntimeError("postgresql://user:startup-secret@host/db")

    with pytest.raises(
        ProjectSkillStartupError,
        match="^project skill database revision is unavailable$",
    ) as captured:
        validate_project_skills_startup(True, secret_bearing_factory)

    assert all(
        "startup-secret" not in str(error)
        for error in _complete_exception_chain(captured.value)
    )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
