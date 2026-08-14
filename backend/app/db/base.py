import sys

from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


import app.audit.models  # noqa: E402,F401
import app.approvals.models  # noqa: E402,F401
import app.conversations.models  # noqa: E402,F401
import app.db.platform_models  # noqa: E402,F401
import app.identity.models  # noqa: E402,F401
import app.runtime.execution_snapshot  # noqa: E402,F401


def _identity_before_flush(session, flush_context, instances):
    from app.identity.catalogue import enforce_identity_before_flush

    enforce_identity_before_flush(session, flush_context, instances)


def _identity_orm_execute(execute_state):
    from app.identity.catalogue import enforce_identity_orm_execute

    enforce_identity_orm_execute(execute_state)


event.listen(Session, "before_flush", _identity_before_flush)
event.listen(Session, "do_orm_execute", _identity_orm_execute)

identity_models = sys.modules["app.identity.models"]
if hasattr(identity_models, "Menu"):
    import app.identity.catalogue  # noqa: E402,F401
