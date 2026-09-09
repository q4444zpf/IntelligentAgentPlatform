from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.database import SessionFactory
from app.core.request_context import RequestContext, require_request_context

from .package import MAX_ZIP_BYTES
from .project_errors import ProjectSkillError
from .project_schemas import (
    IdempotencyKey,
    ProjectSkillCreate,
    ProjectSkillDraftUpdate,
    ProjectSkillListQuery,
    ProjectSkillPageQuery,
    ProjectSkillPublish,
    PublishedSkillInfo,
    SkillDraftInfo,
    SkillImportResult,
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


def _manage_context(
    context: Annotated[RequestContext, Depends(require_request_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
) -> RequestContext:
    try:
        service.check_access(context, "skill.manage")
    except ProjectSkillError as error:
        _raise_http(error)
    return context


async def _validate_import_fields(request: Request) -> None:
    form = await request.form()
    counts: dict[str, int] = {}
    for name, value in form.multi_items():
        counts[name] = counts.get(name, 0) + 1
        if (
            name not in {"file", "manifest"}
            or (name == "file" and not isinstance(value, StarletteUploadFile))
            or (name == "manifest" and (not isinstance(value, str) or value == ""))
        ):
            raise HTTPException(422, "skill_import_manifest_invalid")
    if counts.get("file") != 1 or counts.get("manifest", 0) > 1:
        raise HTTPException(422, "skill_import_manifest_invalid")


def _read_upload(file: UploadFile) -> bytes:
    chunks = []
    size = 0
    try:
        while chunk := file.file.read(min(65536, MAX_ZIP_BYTES + 1 - size)):
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise ProjectSkillError("skill_upload_too_large", 413)
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        file.file.close()


@router.post("/import", response_model=SkillImportResult)
def import_skills(
    response: Response,
    context: Annotated[RequestContext, Depends(_manage_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
    fields: Annotated[None, Depends(_validate_import_fields)],
    file: Annotated[UploadFile, File()],
    manifest: Annotated[str | None, Form()] = None,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.import_bundle(context, _read_upload(file), manifest)
    except ProjectSkillError as error:
        _raise_http(error)


@router.post("", response_model=SkillSummary, status_code=201)
def create_skill(
    request: ProjectSkillCreate,
    response: Response,
    context: Annotated[RequestContext, Depends(_manage_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.create(context, request)
    except ProjectSkillError as error:
        _raise_http(error)


@router.put("/{skill_id}/draft", response_model=SkillDraftInfo)
def save_skill_draft(
    skill_id: UUID,
    request: ProjectSkillDraftUpdate,
    response: Response,
    context: Annotated[RequestContext, Depends(_manage_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.save_draft(context, str(skill_id), request)
    except ProjectSkillError as error:
        _raise_http(error)


@router.post("/{skill_id}/publish", response_model=PublishedSkillInfo)
def publish_skill(
    skill_id: UUID,
    request: ProjectSkillPublish,
    response: Response,
    idempotency_key: Annotated[IdempotencyKey, Header()],
    context: Annotated[RequestContext, Depends(_manage_context)],
    service: Annotated[ProjectSkillService, Depends(_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.publish(context, str(skill_id), request, idempotency_key)
    except ProjectSkillError as error:
        _raise_http(error)


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
