from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from .schemas import SkillCreateRequest, SkillImportResponse, SkillInfo, SkillUpdateRequest
from .service import SkillConflictError, SkillNotFoundError, SkillService, SkillValidationError


def create_router(service: SkillService | None = None) -> APIRouter:
    router = APIRouter()
    manager = service or SkillService()

    def call(operation):
        try:
            return operation()
        except SkillNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"Skill '{error}' was not found") from error
        except SkillConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (SkillValidationError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("", response_model=list[SkillInfo])
    def list_skills():
        return manager.list()

    @router.post("", response_model=SkillInfo, status_code=201)
    def create_skill(request: SkillCreateRequest):
        return call(lambda: manager.create(request))

    @router.post("/import", response_model=SkillImportResponse)
    async def import_skills(
        file: UploadFile = File(...),
        conflict_strategy: Literal["rename", "overwrite", "skip"] = Query("rename"),
    ):
        data = await file.read(10 * 1024 * 1024 + 1)
        return call(lambda: manager.import_zip(data, conflict_strategy))

    @router.get("/{skill_name}", response_model=SkillInfo)
    def get_skill(skill_name: str):
        return call(lambda: manager.get(skill_name))

    @router.put("/{skill_name}", response_model=SkillInfo)
    def update_skill(skill_name: str, request: SkillUpdateRequest):
        return call(lambda: manager.update(skill_name, request))

    @router.patch("/{skill_name}/toggle", response_model=SkillInfo)
    def toggle_skill(skill_name: str):
        return call(lambda: manager.toggle(skill_name))

    @router.delete("/{skill_name}")
    def delete_skill(skill_name: str):
        call(lambda: manager.delete(skill_name))
        return {"deleted": True}

    return router


router = create_router()
