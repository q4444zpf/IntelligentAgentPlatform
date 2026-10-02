from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.identity.authorization import AuthorizationService
from app.identity.schemas import ResourceScope

from .models import ControlCommand, ControlPolicy
from .schemas import (
    ControlCommandInfo,
    ControlCommandProposeRequest,
    ControlFeedbackRequest,
    ControlPolicyCreateRequest,
    ControlPolicyInfo,
)
from .service import (
    ControlExecutionError,
    ControlForbiddenError,
    ControlIdempotencyConflict,
    ControlService,
    ControlValidationError,
)


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"] = "approve"
    reason: str | None = None


def _safe_error(error: Exception) -> HTTPException:
    if isinstance(error, ControlForbiddenError):
        if "not found" in str(error).lower():
            return HTTPException(status_code=404, detail="Control resource does not exist or is not visible")
        return HTTPException(status_code=403, detail="Control operation is not permitted")
    if isinstance(error, ControlIdempotencyConflict):
        return HTTPException(status_code=409, detail="Idempotency key conflict")
    if isinstance(error, ControlValidationError):
        if "expired" in str(error).lower():
            return HTTPException(status_code=410, detail="Control command has expired")
        return HTTPException(status_code=422, detail="Control request is invalid")
    if isinstance(error, ControlExecutionError):
        message = str(error).lower()
        if "executor" in message:
            return HTTPException(status_code=503, detail="Control executor is unavailable")
        return HTTPException(status_code=409, detail="Control command is in an invalid state")
    return HTTPException(status_code=409, detail="Control operation failed")


def _service(session: Session = Depends(get_session)) -> ControlService:
    return ControlService(session)


def create_router(
    session_factory: Callable[[Session], Session] | None = None,
    *, service_factory: Callable[[Session], ControlService] | None = None,
) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> ControlService:
        if service_factory is not None:
            return service_factory(session)
        return ControlService(session_factory(session) if session_factory else session)

    dependency = _service if session_factory is None and service_factory is None else service

    def require_read(context: RequestContext) -> None:
        authorization = context.authorization_context
        if authorization is None or not AuthorizationService().allows(
            authorization,
            "control.read",
            ResourceScope(context.unit_id, context.project_id, context.user_id),
        ):
            raise HTTPException(status_code=403, detail="Control operation is not permitted")

    @router.get("/policies", response_model=list[ControlPolicyInfo])
    def list_policies(context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        require_read(context)
        return manager.session.scalars(select(ControlPolicy).where(ControlPolicy.unit_id == context.unit_id, ControlPolicy.project_id == context.project_id).order_by(ControlPolicy.created_at)).all()

    @router.post("/policies", response_model=ControlPolicyInfo, status_code=status.HTTP_201_CREATED)
    def create_policy(body: ControlPolicyCreateRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        try:
            return manager.create_policy(context, body)
        except Exception as error:
            raise _safe_error(error) from error

    @router.get("/commands", response_model=list[ControlCommandInfo])
    def list_commands(context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        require_read(context)
        return manager.session.scalars(select(ControlCommand).where(ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id).order_by(ControlCommand.created_at)).all()

    @router.get("/commands/{command_id}", response_model=ControlCommandInfo)
    def get_command(command_id: str, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        require_read(context)
        command = manager.session.scalar(select(ControlCommand).where(ControlCommand.id == command_id, ControlCommand.unit_id == context.unit_id, ControlCommand.project_id == context.project_id))
        if command is None:
            raise HTTPException(status_code=404, detail="Control command does not exist or is not visible")
        return command

    @router.post("/commands", response_model=ControlCommandInfo, status_code=status.HTTP_201_CREATED)
    def propose(body: ControlCommandProposeRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        try:
            return manager.propose(context, body)
        except Exception as error:
            raise _safe_error(error) from error

    def decide(command_id: str, decision: str, reason: str | None, context: RequestContext, manager: ControlService):
        try:
            return manager.decide(command_id, context, decision, reason)
        except Exception as error:
            raise _safe_error(error) from error

    @router.post("/commands/{command_id}/approve", response_model=ControlCommandInfo)
    def approve(command_id: str, body: ApprovalRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return decide(command_id, "approve", body.reason if body else None, context, manager)

    @router.post("/commands/{command_id}/reject", response_model=ControlCommandInfo)
    def reject(command_id: str, body: ApprovalRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return decide(command_id, "reject", body.reason if body else None, context, manager)

    @router.post("/commands/{command_id}/execute", response_model=ControlCommandInfo)
    def execute(command_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key"), context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        try:
            return manager.execute(command_id, context, idempotency_key)
        except Exception as error:
            raise _safe_error(error) from error

    @router.post("/commands/{command_id}/feedback", response_model=ControlCommandInfo)
    def feedback(command_id: str, body: ControlFeedbackRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        try:
            return manager.record_feedback(command_id, context, body)
        except Exception as error:
            raise _safe_error(error) from error

    @router.post("/commands/{command_id}/rollback", response_model=ControlCommandInfo)
    def rollback(command_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key"), context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        try:
            return manager.rollback(command_id, context, idempotency_key)
        except Exception as error:
            raise _safe_error(error) from error

    return router


router = create_router()
