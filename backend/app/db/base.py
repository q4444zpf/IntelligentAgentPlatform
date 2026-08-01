from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


import app.conversations.models  # noqa: E402,F401
import app.db.platform_models  # noqa: E402,F401
