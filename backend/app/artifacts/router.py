from collections.abc import Callable

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .schemas import ArtifactDownloadInfo, ArtifactInfo
from .service import ArtifactNotFoundError, ArtifactService
from .storage import S3ObjectStorage


def create_router(session_factory: Callable[[Session], Session] | None = None, *, storage: S3ObjectStorage | None = None) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> ArtifactService:
        return ArtifactService(session_factory(session) if session_factory else session, storage or S3ObjectStorage())

    def not_found(error: Exception):
        raise HTTPException(status_code=404, detail="Artifact does not exist or is not visible") from error

    @router.post("/artifacts", response_model=ArtifactInfo, status_code=status.HTTP_201_CREATED)
    async def upload_artifact(
        file: UploadFile = File(...),
        scope: str = Form("project"),
        run_id: str | None = Form(None),
        context: RequestContext = Depends(require_request_context),
        manager: ArtifactService = Depends(service),
    ):
        if scope not in {"private", "project", "tenant", "public"}:
            raise HTTPException(status_code=422, detail="Invalid artifact scope")
        if not file.filename or "/" in file.filename or "\\" in file.filename:
            raise HTTPException(status_code=422, detail="filename must be a base name")
        data = await file.read()
        try:
            return manager.create(context=context, filename=file.filename, content_type=file.content_type or "application/octet-stream", data=data, scope=scope, run_id=run_id)
        except ArtifactNotFoundError as error:
            not_found(error)

    @router.get("/artifacts", response_model=list[ArtifactInfo])
    def list_artifacts(context: RequestContext = Depends(require_request_context), manager: ArtifactService = Depends(service)):
        return manager.list(context)

    @router.get("/artifacts/{artifact_id}", response_model=ArtifactInfo)
    def get_artifact(artifact_id: str, context: RequestContext = Depends(require_request_context), manager: ArtifactService = Depends(service)):
        try:
            return manager.get(artifact_id, context)
        except ArtifactNotFoundError as error:
            not_found(error)

    @router.get("/artifacts/{artifact_id}/download", response_model=ArtifactDownloadInfo)
    def download_artifact(artifact_id: str, expires_in: int = Query(900, ge=60, le=86400), context: RequestContext = Depends(require_request_context), manager: ArtifactService = Depends(service)):
        try:
            artifact = manager.get(artifact_id, context)
        except ArtifactNotFoundError as error:
            not_found(error)
        effective_expiry = min(expires_in, 900)
        return {"artifact": artifact, "url": manager.storage.presigned_get_url(artifact.object_key, effective_expiry), "expires_in": effective_expiry}

    @router.delete("/artifacts/{artifact_id}", response_model=ArtifactInfo)
    def delete_artifact(artifact_id: str, context: RequestContext = Depends(require_request_context), manager: ArtifactService = Depends(service)):
        try:
            return manager.delete(artifact_id, context)
        except ArtifactNotFoundError as error:
            not_found(error)

    @router.post("/runs/{run_id}/artifacts/{artifact_id}", response_model=ArtifactInfo)
    def attach_artifact(
        run_id: str,
        artifact_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ArtifactService = Depends(service),
    ):
        try:
            return manager.attach_to_run(artifact_id, run_id, context)
        except ArtifactNotFoundError as error:
            not_found(error)

    return router


router = create_router()
