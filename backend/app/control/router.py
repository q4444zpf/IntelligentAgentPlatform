from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .schemas import (
    ControlCommandDetail, ControlCommandInfo, ControlCommandListItem,
    ControlCommandProposeRequest, ControlFeedbackRequest, ControlPolicyCreateRequest,
    ControlPolicyInfo, ControlPolicyUpdateRequest,
)
from .service import ControlService


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


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

    @router.get("/policies", response_model=list[ControlPolicyInfo])
    def list_policies(enabled: bool | None = None, asset_type: str | None = None, command_type: str | None = None,
                      context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.list_policies(context, enabled=enabled, asset_type=asset_type, command_type=command_type)

    @router.post("/policies", response_model=ControlPolicyInfo, status_code=status.HTTP_201_CREATED)
    def create_policy(body: ControlPolicyCreateRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.create_policy(context, body)

    @router.get("/policies/{policy_id}", response_model=ControlPolicyInfo)
    def get_policy(policy_id: str, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.get_policy(context, policy_id)

    @router.patch("/policies/{policy_id}", response_model=ControlPolicyInfo)
    def update_policy(policy_id: str, body: ControlPolicyUpdateRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.update_policy(context, policy_id, body)

    @router.get("/commands", response_model=list[ControlCommandListItem])
    def list_commands(status: str | None = None, policy_id: str | None = None, created_after: datetime | None = None,
                      created_before: datetime | None = None, context: RequestContext = Depends(require_request_context),
                      manager: ControlService = Depends(dependency)):
        return manager.list_commands(context, status=status, policy_id=policy_id, created_after=created_after, created_before=created_before)

    @router.get("/commands/{command_id}", response_model=ControlCommandDetail)
    def get_command(command_id: str, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.get_command_detail(context, command_id)

    @router.post("/commands", response_model=ControlCommandInfo, status_code=status.HTTP_201_CREATED)
    def propose(body: ControlCommandProposeRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.propose(context, body)

    @router.post("/commands/{command_id}/approve", response_model=ControlCommandInfo)
    def approve(command_id: str, body: ApprovalRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.decide(command_id, context, "approve", body.reason if body else None)

    @router.post("/commands/{command_id}/reject", response_model=ControlCommandInfo)
    def reject(command_id: str, body: ApprovalRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.decide(command_id, context, "reject", body.reason if body else None)

    @router.post("/commands/{command_id}/execute", response_model=ControlCommandInfo)
    def execute(command_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key"), context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.execute(command_id, context, idempotency_key)

    @router.post("/commands/{command_id}/feedback", response_model=ControlCommandInfo)
    def feedback(command_id: str, body: ControlFeedbackRequest, context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.record_feedback(command_id, context, body)

    @router.post("/commands/{command_id}/rollback", response_model=ControlCommandInfo)
    def rollback(command_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key"), context: RequestContext = Depends(require_request_context), manager: ControlService = Depends(dependency)):
        return manager.rollback(command_id, context, idempotency_key)

    return router


router = create_router()
