from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.audit.recorder import AuditRecorder
from app.core.request_context import RequestContext
from app.identity.models import AuthSession

from .package_storage import SkillPackageStorage, create_default_skill_package_storage
from .project_access import SkillAccess, require_skill_access
from .project_errors import ProjectSkillError
from .project_schemas import (
    PublishedSkillInfo,
    SkillDraftInfo,
    SkillPage,
    SkillSummary,
    SkillVersionInfo,
    SkillVersionPage,
)
from .repository import SkillRepository


class ProjectSkillService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        storage_factory: Callable[
            [], SkillPackageStorage
        ] = create_default_skill_package_storage,
        audit_recorder: AuditRecorder | None = None,
    ):
        self._session_factory = session_factory
        self._storage_factory = storage_factory
        self._audit_recorder = audit_recorder

    def list(
        self,
        context: RequestContext,
        *,
        offset: int = 0,
        limit: int = 20,
        q: str | None = None,
    ) -> SkillPage:
        with self._session_factory() as session:
            access = self._access(session, context)
            rows, total = SkillRepository(session).list_summaries(
                access.scope,
                owner_ids=access.owner_ids,
                offset=offset,
                limit=limit,
                q=q,
            )
            return SkillPage(
                items=[SkillSummary(**dict(row)) for row in rows],
                total=total,
                offset=offset,
                limit=limit,
            )

    def get(self, context: RequestContext, skill_id: str) -> SkillSummary:
        with self._session_factory() as session:
            access = self._access(session, context)
            row = SkillRepository(session).get_summary(
                access.scope, skill_id, owner_ids=access.owner_ids
            )
            return SkillSummary(**dict(self._found(row)))

    def get_draft(self, context: RequestContext, skill_id: str) -> SkillDraftInfo:
        with self._session_factory() as session:
            access = self._access(session, context)
            draft = SkillRepository(session).get_draft(
                access.scope, skill_id, owner_ids=access.owner_ids
            )
            if draft is None:
                raise ProjectSkillError("skill_not_found", 404)
            return SkillDraftInfo(
                skill_id=draft.skill_id,
                name=draft.name,
                description=draft.description,
                display_version=draft.display_version,
                revision=draft.revision,
                content=draft.content,
                files=draft.files,
                package_digest=draft.package_digest,
                updated_at=draft.updated_at,
            )

    def list_versions(
        self,
        context: RequestContext,
        skill_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> SkillVersionPage:
        with self._session_factory() as session:
            access = self._access(session, context)
            repository = SkillRepository(session)
            self._found(
                repository.get_summary(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
            )
            rows, total = repository.list_version_summaries(
                access.scope,
                skill_id,
                owner_ids=access.owner_ids,
                offset=offset,
                limit=limit,
            )
            return SkillVersionPage(
                items=[PublishedSkillInfo(**dict(row)) for row in rows],
                total=total,
                offset=offset,
                limit=limit,
            )

    def get_version(
        self, context: RequestContext, skill_id: str, version_id: str
    ) -> SkillVersionInfo:
        with self._session_factory() as session:
            access = self._access(session, context)
            version = SkillRepository(session).get_version(
                access.scope,
                skill_id,
                version_id,
                owner_ids=access.owner_ids,
            )
            if version is None:
                raise ProjectSkillError("skill_not_found", 404)
            return SkillVersionInfo(
                id=version.id,
                skill_id=version.skill_id,
                version=version.version,
                source_revision=version.source_revision,
                name=version.name,
                description=version.description,
                display_version=version.display_version,
                package_digest=version.package_digest,
                published_by=version.published_by,
                published_at=version.published_at,
                content=version.content,
                files=version.files,
            )

    @staticmethod
    def _access(session: Session, context: RequestContext) -> SkillAccess:
        access = require_skill_access(session, context, "skill.read")
        authorization = context.authorization_context
        if authorization is None:
            raise ProjectSkillError("skill_authentication_required", 401)
        persisted = session.get(AuthSession, authorization.session_id)
        if persisted is not None and persisted.current_project_id != context.project_id:
            raise ProjectSkillError("skill_project_required", 403)
        return access

    @staticmethod
    def _found(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if row is None:
            raise ProjectSkillError("skill_not_found", 404)
        return row
