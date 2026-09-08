from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.database import SessionFactory
from app.core.request_context import RequestContext, require_request_context

from .project_errors import ProjectSkillError
from .project_schemas import (
    ProjectSkillListQuery,
    ProjectSkillPageQuery,
    SkillDraftInfo,
    SkillPage,
    SkillSummary,
    SkillVersionInfo,
    SkillVersionPage,
)
from .project_service import ProjectSkillService

router = APIRouter(prefix="/api/project-skills", tags=["project-skills"])


def _service() -> ProjectSkillService:
    return ProjectSkillService(SessionFactory)


def _raise_http(error: ProjectSkillError) -> None:
    raise HTTPException(error.status_code, error.code) from error


@router.get("", response_model=SkillPage)
def list_skills(
    response: Response,
    query: Annotated[ProjectSkillListQuery, Query()],
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.list(context, offset=query.offset, limit=query.limit, q=query.q)
    except ProjectSkillError as error:
        _raise_http(error)


@router.get("/{skill_id}", response_model=SkillSummary)
def get_skill(
    skill_id: UUID,
    response: Response,
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.get(context, str(skill_id))
    except ProjectSkillError as error:
        _raise_http(error)


@router.get("/{skill_id}/draft", response_model=SkillDraftInfo)
def get_skill_draft(
    skill_id: UUID,
    response: Response,
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.get_draft(context, str(skill_id))
    except ProjectSkillError as error:
        _raise_http(error)


@router.get("/{skill_id}/versions", response_model=SkillVersionPage)
def list_skill_versions(
    skill_id: UUID,
    response: Response,
    query: Annotated[ProjectSkillPageQuery, Query()],
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.list_versions(
            context, str(skill_id), offset=query.offset, limit=query.limit
        )
    except ProjectSkillError as error:
        _raise_http(error)


@router.get("/{skill_id}/versions/{version_id}", response_model=SkillVersionInfo)
def get_skill_version(
    skill_id: UUID,
    version_id: UUID,
    response: Response,
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.get_version(context, str(skill_id), str(version_id))
    except ProjectSkillError as error:
        _raise_http(error)
