from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import create_session_factory


def test_session_factory_executes_sqlite_for_unit_tests():
    factory = create_session_factory("sqlite+pysqlite:///:memory:")
    with factory() as session:
        assert session.scalar(text("select 1")) == 1
        assert isinstance(session, Session)
