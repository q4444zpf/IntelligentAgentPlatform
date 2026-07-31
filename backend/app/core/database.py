from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


SessionFactory = create_session_factory(settings.database_url)


def get_session() -> Generator[Session, None, None]:
    with SessionFactory() as session:
        yield session
