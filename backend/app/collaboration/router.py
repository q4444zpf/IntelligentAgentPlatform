from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .repository import TeamDefinitionValidationError, TeamDraftConflictError, TeamNotFoundError
from .schemas import TeamCreateRequest, TeamDraftUpdate, TeamMetadataUpdate
from .service import TeamPermissionError, TeamService, TeamUnavailableError

router = APIRouter(dependencies=[Depends(require_request_context)])


def _service(session=Depends(get_session)) -> TeamService:
    return TeamService(session)


def _error(error: Exception) -> HTTPException:
    if isinstance(error, TeamNotFoundError):
        return HTTPException(404, "team_not_found")
    if isinstance(error, TeamPermissionError):
        return HTTPException(403, str(error))
    if isinstance(error, TeamDraftConflictError):
        return HTTPException(409, "team_draft_conflict")
    if isinstance(error, TeamUnavailableError):
        return HTTPException(422, "team_unavailable")
    return HTTPException(422, "team_definition_invalid")


@router.get("/teams")
def list_teams(context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.list(context)
    except Exception as error:
        raise _error(error) from error


@router.post("/teams", status_code=201)
def create_team(request: TeamCreateRequest, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.create(context, request)
    except Exception as error:
        raise _error(error) from error


@router.get("/teams/{team_id}")
def get_team(team_id: str, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.get(context, team_id)
    except Exception as error:
        raise _error(error) from error


@router.get("/teams/{team_id}/versions")
def list_team_versions(team_id: str, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.list_versions(context, team_id)
    except Exception as error:
        raise _error(error) from error


@router.get("/teams/{team_id}/versions/{version}")
def get_team_version(team_id: str, version: int, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.get_version(context, team_id, version)
    except Exception as error:
        raise _error(error) from error


@router.patch("/teams/{team_id}")
def update_team(team_id: str, request: TeamMetadataUpdate, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.update(context, team_id, request)
    except Exception as error:
        raise _error(error) from error


@router.put("/teams/{team_id}/draft")
def save_draft(team_id: str, request: TeamDraftUpdate, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.save_draft(context, team_id, request)
    except Exception as error:
        raise _error(error) from error


@router.post("/teams/{team_id}/publish")
def publish(team_id: str, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.publish(context, team_id)
    except Exception as error:
        raise _error(error) from error


@router.post("/teams/{team_id}/enable")
def enable(team_id: str, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.set_enabled(context, team_id, True)
    except Exception as error:
        raise _error(error) from error


@router.post("/teams/{team_id}/disable")
def disable(team_id: str, context: RequestContext = Depends(require_request_context), service: TeamService = Depends(_service)):
    try:
        return service.set_enabled(context, team_id, False)
    except Exception as error:
        raise _error(error) from error
