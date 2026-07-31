from typing import Annotated

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel


class RequestContext(BaseModel):
    user_id: str
    project_id: str


def require_request_context(
    request: Request,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
) -> RequestContext:
    if not getattr(request.app.state, "allow_dev_identity", False):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not project_id:
        raise HTTPException(
            status_code=401,
            detail="User and project headers are required",
        )
    return RequestContext(user_id=user_id, project_id=project_id)
