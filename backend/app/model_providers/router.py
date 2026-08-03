from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from app.core.request_context import RequestContext, require_request_context
from app.audit.management import management_audit_route_class, management_request_id, record_failed_management

from .schemas import ActiveModel, AddModelRequest, CreateProviderRequest, DiscoverModelsResponse, ModelConfigRequest, ProbeMultimodalResponse, ProviderConfigRequest, ProviderInfo, TestConnectionResponse
from .service import ProviderService
from .store import ConcurrentProviderUpdateError

router = APIRouter(dependencies=[Depends(require_request_context)])
service = ProviderService()
router.route_class = management_audit_route_class(
    lambda: service.store.session_factory, lambda: service.audit_recorder,
    source="llm", resource_type="model_provider",
)


def not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Resource '{error.args[0]}' not found")


def call_management(operation, session, context: RequestContext, request_id: str | None, action: str, resource_id: str, *, value_status: int = 400):
    try:
        return operation()
    except KeyError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_NOT_FOUND", request_id=request_id)
        raise not_found(error) from error
    except ConcurrentProviderUpdateError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_STALE_UPDATE", request_id=request_id)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_VALIDATION", request_id=request_id)
        raise HTTPException(status_code=value_status, detail=str(error)) from error


def require_provider_admin(request: Request, context: RequestContext = Depends(require_request_context)) -> RequestContext:
    if context.role == "admin":
        request.state.management_context = context
        management_request_id(request)
        return context
    resource_id = request.path_params.get("provider_id", "providers")
    if request.path_params.get("model_id"):
        resource_id = f"{resource_id}/{request.path_params['model_id']}"
    action = "resource.deleted" if request.method == "DELETE" else "resource.created" if request.method == "POST" and "custom-providers" in request.url.path else "resource.updated"
    record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PERMISSION_DENIED", request_id=management_request_id(request))
    raise HTTPException(status_code=403, detail="Administrator permission is required")
async def call_management_async(operation, session, context: RequestContext, request_id: str | None, action: str, resource_id: str):
    try:
        return await operation()
    except KeyError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_NOT_FOUND", request_id=request_id)
        raise not_found(error) from error
    except ConcurrentProviderUpdateError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_STALE_UPDATE", request_id=request_id)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        record_failed_management(service.store.session_factory, service.audit_recorder, context, source="llm", action=action, resource_type="model_provider", resource_id=resource_id, error_code="PROVIDER_VALIDATION", request_id=request_id)
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.get("", response_model=list[ProviderInfo])
def list_providers(): return service.list()

@router.post("/custom-providers", response_model=ProviderInfo, status_code=201)
def create_provider(body: CreateProviderRequest, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.create(body, context=context, session=session, request_id=request_id), session, context, request_id, "resource.created", body.id, value_status=409)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.put("/{provider_id}/config", response_model=ProviderInfo)
def configure_provider(
    provider_id: str, body: ProviderConfigRequest,
    context: RequestContext = Depends(require_provider_admin),
    request_id: str = Depends(management_request_id),
):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.configure(provider_id, body, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", provider_id)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/{provider_id}/models", response_model=ProviderInfo)
def add_model(provider_id: str, body: AddModelRequest, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.add_model(provider_id, body, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", f"{provider_id}/{body.id}", value_status=409)
    except KeyError as exc: raise not_found(exc) from exc
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.put("/{provider_id}/models/{model_id}/config", response_model=ProviderInfo)
def configure_model(provider_id: str, model_id: str, body: ModelConfigRequest, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.configure_model(provider_id, model_id, body, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", f"{provider_id}/{model_id}")
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.delete("/{provider_id}/models/{model_id}", response_model=ProviderInfo)
def remove_model(provider_id: str, model_id: str, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.remove_model(provider_id, model_id, context=context, session=session, request_id=request_id), session, context, request_id, "resource.deleted", f"{provider_id}/{model_id}")
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{provider_id}/discover", response_model=DiscoverModelsResponse)
async def discover_models(provider_id: str, save: bool = True, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return await call_management_async(lambda: service.discover_models(provider_id, save, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", provider_id)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{provider_id}/models/{model_id}/probe-multimodal", response_model=ProbeMultimodalResponse)
async def probe_multimodal(provider_id: str, model_id: str, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return await call_management_async(lambda: service.probe_multimodal(provider_id, model_id, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", f"{provider_id}/{model_id}")
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{provider_id}/test", response_model=TestConnectionResponse)
async def test_provider(provider_id: str, body: ProviderConfigRequest | None = Body(default=None)):
    try: return await service.test(provider_id, override=body)
    except KeyError as exc: raise not_found(exc) from exc

@router.post("/{provider_id}/models/{model_id}/test", response_model=TestConnectionResponse)
async def test_model(provider_id: str, model_id: str):
    try: return await service.test(provider_id, model_id)
    except KeyError as exc: raise not_found(exc) from exc

@router.get("/active", response_model=ActiveModel)
def get_active(): return service.get_active()

@router.put("/active", response_model=ActiveModel)
def set_active(body: ActiveModel, context: RequestContext = Depends(require_provider_admin), request_id: str = Depends(management_request_id)):
    try:
        with service.store.session_factory() as session:
            return call_management(lambda: service.set_active(body, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", f"{body.provider_id}/{body.model}")
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
