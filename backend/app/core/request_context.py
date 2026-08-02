from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel


class RequestContext(BaseModel):
    user_id: str
    project_id: str
    role: Literal["user", "admin"] = "user"


def require_request_context(
    request: Request,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
    role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> RequestContext:
    if not getattr(request.app.state, "allow_dev_identity", False):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not project_id:
        raise HTTPException(
            status_code=401,
            detail="User and project headers are required",
        )
    if role not in {None, "user", "admin"}:
        raise HTTPException(status_code=401, detail="Invalid development identity")
    return RequestContext(
        user_id=user_id,
        project_id=project_id,
        role=role or "user",
    )


def require_admin_context(
    context: RequestContext = Depends(require_request_context),
) -> RequestContext:
    if context.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator permission is required",
        )
    return context
