from typing import Annotated

from fastapi import Depends, HTTPException, Request

from .authorization import AuthorizationService
from .schemas import AuthorizationContext


def require_authorization_context(request: Request) -> AuthorizationContext:
    context = getattr(request.state, "authorization_context", None)
    if not isinstance(context, AuthorizationContext):
        raise HTTPException(status_code=401, detail="Authentication is required")
    return context


def require_project_context(
    context: Annotated[AuthorizationContext, Depends(require_authorization_context)],
) -> AuthorizationContext:
    if context.current_project_id is None:
        raise HTTPException(status_code=409, detail="AUTH_CONTEXT_CHANGED")
    return context


def require_scoped_permission(code: str):
    def dependency(
        context: Annotated[AuthorizationContext, Depends(require_authorization_context)],
    ) -> AuthorizationContext:
        if not any(grant.permission_code == code for grant in context.grants):
            raise HTTPException(status_code=403, detail="Permission denied")
        return context
    return dependency


def require_permission(code: str, project_required: bool = False):
    def dependency(
        context: Annotated[AuthorizationContext, Depends(require_authorization_context)],
    ) -> AuthorizationContext:
        target = "current_project" if project_required else "unit"
        allowed = AuthorizationService().allows_entry(context, code, target)
        if project_required:
            # Project routes require an explicit project-scoped grant. A unit
            # grant may be usable while viewing a project, but must not admit
            # project-only APIs or menus.
            allowed = allowed and any(
                grant.permission_code == code
                and grant.data_scope != "unit"
                and context.current_project_id in grant.project_ids
                for grant in context.grants
            )
        if not allowed:
            status = 409 if project_required and context.current_project_id is None else 403
            raise HTTPException(status_code=status, detail="Permission denied")
        return context
    return dependency
