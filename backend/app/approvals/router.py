from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.conversations.models import AgentRun

from .schemas import ApprovalDecisionRequest, ApprovalInfo
from .service import ApprovalConflictError, ApprovalForbiddenError, ApprovalNotFoundError, ApprovalService


def create_router(service_factory: Callable[[Session], ApprovalService] | None = None) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> ApprovalService:
        return service_factory(session) if service_factory else ApprovalService(session)

    def get_or_404(manager: ApprovalService, approval_id: str, context: RequestContext):
        try:
            return manager.get(approval_id, context)
        except ApprovalNotFoundError as error:
            raise HTTPException(status_code=404, detail="审批单不存在或不属于当前项目") from error

    @router.get("", response_model=list[ApprovalInfo])
    def list_approvals(
        status: str = Query("pending", pattern="^(pending|approved|rejected|expired|cancelled|all)$"),
        context: RequestContext = Depends(require_request_context),
        manager: ApprovalService = Depends(service),
    ):
        if status == "pending":
            return manager.list_pending(context)
        rows = manager.list_pending(context) if status == "pending" else manager.list_all(context, status)
        return rows

    @router.get("/{approval_id}", response_model=ApprovalInfo)
    def get_approval(approval_id: str, context: RequestContext = Depends(require_request_context), manager: ApprovalService = Depends(service)):
        return get_or_404(manager, approval_id, context)

    @router.post("/{approval_id}/approve", response_model=ApprovalInfo)
    def approve_approval(approval_id: str, body: ApprovalDecisionRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ApprovalService = Depends(service)):
        try:
            approval = manager.approve(approval_id, context, body.reason if body else None)
            run = manager.session.get(AgentRun, approval.run_id)
            if run is not None:
                run.status = "queued"
            manager.session.flush()
            from app.conversations.repository import ConversationRepository
            ConversationRepository(manager.session).append_event(approval.run_id, "approval.resolved", {"approval_id": approval.id, "status": "approved"})
            ConversationRepository(manager.session).append_event(approval.run_id, "run.status", {"status": "queued"})
            manager.session.commit()
            from app.conversations.router import default_run_dispatcher
            default_run_dispatcher.resume_approval(approval.id)
            return approval
        except ApprovalNotFoundError as error:
            raise HTTPException(status_code=404, detail="审批单不存在或不属于当前项目") from error
        except ApprovalForbiddenError as error:
            raise HTTPException(status_code=403, detail="无权审批该审批单") from error
        except ApprovalConflictError as error:
            manager.session.rollback()
            status_code = 410 if "expired" in str(error) else 409
            raise HTTPException(status_code=status_code, detail=str(error)) from error

    @router.post("/{approval_id}/reject", response_model=ApprovalInfo)
    def reject_approval(approval_id: str, body: ApprovalDecisionRequest | None = None, context: RequestContext = Depends(require_request_context), manager: ApprovalService = Depends(service)):
        try:
            approval = manager.reject(approval_id, context, body.reason if body else None)
            manager.session.commit()
            return approval
        except ApprovalNotFoundError as error:
            raise HTTPException(status_code=404, detail="审批单不存在或不属于当前项目") from error
        except ApprovalForbiddenError as error:
            raise HTTPException(status_code=403, detail="无权审批该审批单") from error
        except ApprovalConflictError as error:
            manager.session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router


router = create_router()
