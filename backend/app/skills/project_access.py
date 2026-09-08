from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import RequestContext
from app.identity.authorization import AuthorizationService
from app.identity.models import Project
from app.identity.schemas import ResourceScope

from .project_errors import ProjectSkillError
from .repository import SkillScope


@dataclass(frozen=True)
class SkillAccess:
    scope: SkillScope
    owner_ids: frozenset[str] | None


def require_skill_access(
    session: Session,
    context: RequestContext,
    permission: str,
) -> SkillAccess:
    authorization = context.authorization_context
    if authorization is None or (
        authorization.user_id != context.user_id
        or authorization.unit_id != context.unit_id
        or (authorization.current_project_id or "") != (context.project_id or "")
    ):
        raise ProjectSkillError("skill_authentication_required", 401)

    if not context.project_id:
        raise ProjectSkillError("skill_project_required", 403)
    project_exists = session.scalar(
        select(Project.id).where(
            Project.id == context.project_id,
            Project.unit_id == context.unit_id,
            Project.status == "active",
        )
    )
    if project_exists is None:
        raise ProjectSkillError("skill_project_required", 403)

    scope = SkillScope(context.unit_id, context.project_id)
    owners: set[str] = set()
    authorization_service = AuthorizationService()
    for grant in authorization.grants:
        single_grant = authorization.model_copy(update={"grants": (grant,)})
        owner = (
            grant.owner_user_id or authorization.user_id
            if grant.data_scope == "own"
            else None
        )
        target = ResourceScope(context.unit_id, context.project_id, owner)
        if not authorization_service.allows(single_grant, permission, target):
            continue
        if grant.data_scope != "own":
            return SkillAccess(scope, None)
        owners.add(owner)

    if not owners:
        raise ProjectSkillError("skill_permission_denied", 403)
    return SkillAccess(scope, frozenset(owners))
