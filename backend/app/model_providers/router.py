from fastapi import APIRouter, Body, HTTPException, Path

from .schemas import ActiveModel, AddModelRequest, CreateProviderRequest, DiscoverModelsResponse, ModelConfigRequest, ProbeMultimodalResponse, ProviderConfigRequest, ProviderInfo, TestConnectionResponse
from .service import ProviderService
from .store import ConcurrentProviderUpdateError

router = APIRouter()
service = ProviderService()


def not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Resource '{error.args[0]}' not found")


@router.get("", response_model=list[ProviderInfo])
def list_providers(): return service.list()

@router.post("/custom-providers", response_model=ProviderInfo, status_code=201)
def create_provider(body: CreateProviderRequest):
    try: return service.create(body)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.put("/{provider_id}/config", response_model=ProviderInfo)
def configure_provider(provider_id: str, body: ProviderConfigRequest):
    try: return service.configure(provider_id, body)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/{provider_id}/models", response_model=ProviderInfo)
def add_model(provider_id: str, body: AddModelRequest):
    try: return service.add_model(provider_id, body)
    except KeyError as exc: raise not_found(exc) from exc
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.put("/{provider_id}/models/{model_id}/config", response_model=ProviderInfo)
def configure_model(provider_id: str, model_id: str, body: ModelConfigRequest):
    try: return service.configure_model(provider_id, model_id, body)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.delete("/{provider_id}/models/{model_id}", response_model=ProviderInfo)
def remove_model(provider_id: str, model_id: str):
    try: return service.remove_model(provider_id, model_id)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{provider_id}/discover", response_model=DiscoverModelsResponse)
async def discover_models(provider_id: str, save: bool = True):
    try: return await service.discover_models(provider_id, save)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{provider_id}/models/{model_id}/probe-multimodal", response_model=ProbeMultimodalResponse)
async def probe_multimodal(provider_id: str, model_id: str):
    try: return await service.probe_multimodal(provider_id, model_id)
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
def set_active(body: ActiveModel):
    try: return service.set_active(body)
    except KeyError as exc: raise not_found(exc) from exc
    except ConcurrentProviderUpdateError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
