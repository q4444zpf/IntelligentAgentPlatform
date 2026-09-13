from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.management import (
    ManagementAuditIdentity,
    management_event_id,
    management_trace_id,
)
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext

from .models import Skill, SkillDraft, SkillVersion
from .package import SkillPackageError, ValidatedSkillPackage, parse_skill_bundle
from .package_storage import (
    SkillPackageStorage,
    SkillPackageStorageError,
    StoredSkillPackage,
    create_default_skill_package_storage,
)
from .project_access import SkillAccess, require_skill_access
from .project_errors import ProjectSkillError
from .project_packages import (
    DraftPackageSnapshot,
    package_from_content,
    read_verified_package,
    rename_manifest,
    replace_manifest,
    snapshot_draft,
)
from .project_schemas import (
    IdempotencyKey,
    ProjectSkillCreate,
    ProjectSkillAvailabilityUpdate,
    ProjectSkillDraftUpdate,
    ProjectSkillImportCreate,
    ProjectSkillImportEntry,
    ProjectSkillPublish,
    PublishedSkillInfo,
    SkillDraftInfo,
    SkillImportItem,
    SkillImportResult,
    SkillPage,
    SkillSummary,
    SkillVersionInfo,
    SkillVersionPage,
)
from .repository import (
    SkillIdempotencyConflict,
    SkillRepository,
    SkillResourceNotFound,
    SkillRevisionConflict,
)


@dataclass(frozen=True)
class PreparedSkillImport:
    source_name: str
    action: Literal["create", "update"]
    skill_id: str
    name: str
    expected_snapshot: DraftPackageSnapshot | None
    package: ValidatedSkillPackage
    stored: StoredSkillPackage


_IMPORT_MANIFEST = TypeAdapter(
    Annotated[list[ProjectSkillImportEntry], Field(max_length=500)]
)
_IDEMPOTENCY_KEY = TypeAdapter(IdempotencyKey)
_PUBLISH_REVISION = TypeAdapter(Annotated[int, Field(strict=True, gt=0)])


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

    def check_access(self, context: RequestContext, permission: str) -> SkillAccess:
        with self._session_factory() as session:
            return require_skill_access(session, context, permission)

    def publish(
        self,
        context: RequestContext,
        skill_id: str,
        request: ProjectSkillPublish,
        idempotency_key: str,
    ) -> PublishedSkillInfo:
        try:
            self.check_access(context, "skill.manage")
            try:
                _IDEMPOTENCY_KEY.validate_python(idempotency_key)
                _PUBLISH_REVISION.validate_python(request.expected_revision)
            except ValidationError as error:
                raise ProjectSkillError("skill_publish_request_invalid", 422) from error
            replay = self._publish_replay(context, skill_id, request, idempotency_key)
            if replay is not None:
                return replay
            with self._session_factory() as session:
                access = require_skill_access(session, context, "skill.manage")
                draft = SkillRepository(session).get_draft(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                if draft is None:
                    raise ProjectSkillError("skill_not_found", 404)
                original = (
                    snapshot_draft(draft)
                    if draft.revision == request.expected_revision
                    else None
                )
            if original is None:
                replay = self._publish_replay(
                    context, skill_id, request, idempotency_key
                )
                if replay is not None:
                    return replay
                raise ProjectSkillError("skill_revision_conflict", 409)
            if original.stored.object_key.split("/")[:3] != [
                access.scope.unit_id,
                access.scope.project_id,
                skill_id,
            ]:
                raise ProjectSkillError("skill_storage_unavailable", 503)
            try:
                read_verified_package(self._storage_factory(), original)
            except (SkillPackageStorageError, ProjectSkillError) as error:
                if isinstance(error, ProjectSkillError) and not (
                    error.code == "skill_storage_unavailable"
                    and isinstance(
                        error.__cause__, (SkillPackageError, SkillPackageStorageError)
                    )
                ):
                    raise
                replay = self._publish_replay(
                    context, skill_id, request, idempotency_key
                )
                if replay is not None:
                    return replay
                raise
            with self._session_factory() as session, session.begin():
                access = require_skill_access(session, context, "skill.manage")
                repository = SkillRepository(session)
                skill = repository.lock_skill(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                replay = repository.find_publish_replay(
                    access.scope,
                    skill_id,
                    expected_revision=request.expected_revision,
                    idempotency_key=idempotency_key,
                    published_by=context.user_id,
                    owner_ids=access.owner_ids,
                )
                if replay is not None:
                    return PublishedSkillInfo(
                        **self._version_fields(replay, enabled=skill.enabled)
                    )
                current = repository.get_draft(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                if current is not None:
                    session.refresh(current)
                if current is None or snapshot_draft(current) != original:
                    raise ProjectSkillError("skill_revision_conflict", 409)
                version = repository.publish(
                    access.scope,
                    skill_id,
                    expected_revision=request.expected_revision,
                    idempotency_key=idempotency_key,
                    published_by=context.user_id,
                )
                self._record_change(
                    session,
                    context,
                    skill_id,
                    "skill.publish",
                    revision=version.source_revision,
                    digest=version.package_digest,
                    version_id=version.id,
                )
                result = PublishedSkillInfo(
                    **self._version_fields(version, enabled=skill.enabled)
                )
            return result
        except (
            SkillPackageStorageError,
            SkillRevisionConflict,
            SkillIdempotencyConflict,
            SkillResourceNotFound,
        ) as error:
            self._raise_known(error)
            raise

    def _publish_replay(
        self,
        context: RequestContext,
        skill_id: str,
        request: ProjectSkillPublish,
        idempotency_key: str,
    ) -> PublishedSkillInfo | None:
        with self._session_factory() as session:
            access = require_skill_access(session, context, "skill.manage")
            repository = SkillRepository(session)
            replay = repository.find_publish_replay(
                access.scope,
                skill_id,
                expected_revision=request.expected_revision,
                idempotency_key=idempotency_key,
                published_by=context.user_id,
                owner_ids=access.owner_ids,
            )
            skill = repository.get(
                access.scope, skill_id, owner_ids=access.owner_ids
            )
            return (
                PublishedSkillInfo(
                    **self._version_fields(
                        replay,
                        enabled=skill.enabled,
                    )
                )
                if replay is not None
                else None
            )

    @staticmethod
    def _version_fields(row: SkillVersion, *, enabled: bool) -> dict[str, object]:
        fields = {
            field: getattr(row, field)
            for field in (
                "id",
                "skill_id",
                "version",
                "source_revision",
                "name",
                "description",
                "display_version",
                "package_digest",
                "object_key",
                "archive_sha256",
                "size_bytes",
                "published_by",
                "published_at",
            )
        }
        fields["enabled"] = enabled
        return fields

    def import_bundle(
        self, context: RequestContext, data: bytes, manifest_json: str | None
    ) -> SkillImportResult:
        self.check_access(context, "skill.manage")
        try:
            packages = parse_skill_bundle(data)
            entries = self._import_entries(packages, manifest_json)
            by_name = {package.name: package for package in packages}
            update_ids = [
                str(entry.skill_id) for entry in entries if entry.action == "update"
            ]
            planned = []
            results = {}
            create_names = set()
            with self._session_factory() as session:
                access = require_skill_access(session, context, "skill.manage")
                repository = SkillRepository(session)
                for entry in entries:
                    if entry.action == "skip":
                        results[entry.source_name] = SkillImportItem(
                            source_name=entry.source_name,
                            action="skip",
                            skill_id=None,
                            name=entry.source_name,
                            draft_revision=None,
                        )
                        continue
                    original = None
                    if entry.action == "create":
                        self._require_create_owner(context, access)
                        name = entry.target_name or entry.source_name
                        if name in create_names or repository.name_exists(
                            access.scope, name
                        ):
                            raise ProjectSkillError("skill_name_conflict", 409)
                        create_names.add(name)
                        skill_id = str(uuid4())
                    else:
                        skill_id = str(entry.skill_id)
                        draft = repository.get_draft(
                            access.scope, skill_id, owner_ids=access.owner_ids
                        )
                        if draft is None:
                            raise ProjectSkillError("skill_not_found", 404)
                        if draft.revision != entry.expected_revision:
                            raise ProjectSkillError("skill_revision_conflict", 409)
                        original = snapshot_draft(draft)
                        name = original.name
                    package = by_name[entry.source_name]
                    if package.name != name:
                        package = rename_manifest(package, name)
                    planned.append(
                        (
                            entry.source_name,
                            entry.action,
                            skill_id,
                            name,
                            original,
                            package,
                        )
                    )
            if not planned:
                return SkillImportResult(
                    items=[results[entry.source_name] for entry in entries],
                    created_count=0,
                    updated_count=0,
                    skipped_count=len(entries),
                )
            # Every target is authorized before object I/O; no session or locks span it.
            storage = self._storage_factory()
            prepared = [
                PreparedSkillImport(
                    source_name,
                    action,
                    skill_id,
                    name,
                    original,
                    package,
                    stored=storage.put(
                        access.scope.unit_id, access.scope.project_id, skill_id, package
                    ),
                )
                for source_name, action, skill_id, name, original, package in planned
            ]
            trace_id = str(uuid4())
            with self._session_factory() as session, session.begin():
                access = require_skill_access(session, context, "skill.manage")
                repository = SkillRepository(session)
                for skill_id in sorted(update_ids):
                    repository.lock_skill(
                        access.scope, skill_id, owner_ids=access.owner_ids
                    )
                for item in prepared:
                    results[item.source_name] = self._apply_import(
                        session, repository, context, access, item, trace_id
                    )
            return SkillImportResult(
                items=[results[entry.source_name] for entry in entries],
                created_count=len(create_names),
                updated_count=len(update_ids),
                skipped_count=len(entries) - len(prepared),
            )
        except (
            SkillPackageError,
            SkillPackageStorageError,
            SkillRevisionConflict,
            SkillResourceNotFound,
            IntegrityError,
        ) as error:
            self._raise_known(error)
            raise

    def set_enabled(
        self,
        context: RequestContext,
        skill_id: str,
        request: ProjectSkillAvailabilityUpdate,
        *,
        request_id: ManagementAuditIdentity | None = None,
    ) -> SkillSummary:
        try:
            with self._session_factory() as session, session.begin():
                access = require_skill_access(session, context, "skill.manage")
                repository = SkillRepository(session)
                skill, draft = repository.set_enabled(
                    access.scope,
                    skill_id,
                    enabled=request.enabled,
                    expected_revision=request.expected_revision,
                    owner_ids=access.owner_ids,
                )
                self._record_change(
                    session,
                    context,
                    skill_id,
                    "skill.availability.update",
                    revision=draft.revision,
                    digest=draft.package_digest,
                    enabled=skill.enabled,
                    request_id=request_id,
                )
                result = SkillSummary(
                    **dict(
                        self._found(
                            repository.get_summary(
                                access.scope,
                                skill_id,
                                owner_ids=access.owner_ids,
                            )
                        )
                    )
                )
            return result
        except (SkillResourceNotFound, SkillRevisionConflict) as error:
            self._raise_known(error)
            raise

    @staticmethod
    def _import_entries(
        packages: tuple[ValidatedSkillPackage, ...], manifest_json: str | None
    ) -> list[ProjectSkillImportEntry]:
        if manifest_json is None:
            return [
                ProjectSkillImportCreate(action="create", source_name=package.name)
                for package in packages
            ]
        try:
            if len(manifest_json.encode("utf-8")) > 256 * 1024:
                raise ProjectSkillError("skill_import_manifest_invalid", 422)
            entries = _IMPORT_MANIFEST.validate_json(manifest_json)
        except (ValidationError, UnicodeEncodeError) as error:
            raise ProjectSkillError("skill_import_manifest_invalid", 422) from error
        names = [entry.source_name for entry in entries]
        update_ids = [
            str(entry.skill_id) for entry in entries if entry.action == "update"
        ]
        if (
            len(names) != len(set(names))
            or set(names) != {package.name for package in packages}
            or len(update_ids) != len(set(update_ids))
        ):
            raise ProjectSkillError("skill_import_manifest_invalid", 422)
        return entries

    def _apply_import(
        self,
        session: Session,
        repository: SkillRepository,
        context: RequestContext,
        access: SkillAccess,
        item: PreparedSkillImport,
        trace_id: str,
    ) -> SkillImportItem:
        if item.action == "create":
            self._require_create_owner(context, access)
            repository.create(
                access.scope,
                skill_id=item.skill_id,
                name=item.name,
                created_by=context.user_id,
                package=item.package,
                stored=item.stored,
            )
            revision = 1
        else:
            original = item.expected_snapshot
            current = repository.get_draft(
                access.scope, item.skill_id, owner_ids=access.owner_ids
            )
            if (
                original is None
                or current is None
                or snapshot_draft(current) != original
            ):
                raise ProjectSkillError("skill_revision_conflict", 409)
            draft = repository.save_draft(
                access.scope,
                item.skill_id,
                expected_revision=original.revision,
                package=item.package,
                stored=item.stored,
            )
            revision = draft.revision
        self._record_change(
            session,
            context,
            item.skill_id,
            "skill.import",
            revision=revision,
            digest=item.stored.package_digest,
            trace_id=trace_id,
        )
        return SkillImportItem(
            source_name=item.source_name,
            action=item.action,
            skill_id=item.skill_id,
            name=item.name,
            draft_revision=revision,
        )

    def create(
        self, context: RequestContext, request: ProjectSkillCreate
    ) -> SkillSummary:
        access = self.check_access(context, "skill.manage")
        self._require_create_owner(context, access)
        try:
            package = package_from_content(request.content)
            with self._session_factory() as session:
                access = require_skill_access(session, context, "skill.manage")
                self._require_create_owner(context, access)
                if (
                    session.scalar(
                        select(Skill.id).where(
                            Skill.unit_id == access.scope.unit_id,
                            Skill.project_id == access.scope.project_id,
                            Skill.name == package.name,
                        )
                    )
                    is not None
                ):
                    raise ProjectSkillError("skill_name_conflict", 409)
            skill_id = str(uuid4())
            stored = self._storage_factory().put(
                access.scope.unit_id, access.scope.project_id, skill_id, package
            )
            with self._session_factory() as session, session.begin():
                access = require_skill_access(session, context, "skill.manage")
                self._require_create_owner(context, access)
                repository = SkillRepository(session)
                repository.create(
                    access.scope,
                    skill_id=skill_id,
                    name=package.name,
                    created_by=context.user_id,
                    package=package,
                    stored=stored,
                )
                self._record_change(
                    session,
                    context,
                    skill_id,
                    "skill.create",
                    revision=1,
                    digest=stored.package_digest,
                )
                result = SkillSummary(
                    **dict(
                        self._found(
                            repository.get_summary(
                                access.scope, skill_id, owner_ids=access.owner_ids
                            )
                        )
                    )
                )
            return result
        except (SkillPackageError, SkillPackageStorageError, IntegrityError) as error:
            self._raise_known(error)
            raise

    def save_draft(
        self, context: RequestContext, skill_id: str, request: ProjectSkillDraftUpdate
    ) -> SkillDraftInfo:
        try:
            with self._session_factory() as session:
                access = require_skill_access(session, context, "skill.manage")
                draft = SkillRepository(session).get_draft(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                if draft is None:
                    raise ProjectSkillError("skill_not_found", 404)
                if draft.revision != request.expected_revision:
                    raise ProjectSkillError("skill_revision_conflict", 409)
                original = snapshot_draft(draft)
            if original.stored.object_key.split("/")[:3] != [
                access.scope.unit_id,
                access.scope.project_id,
                skill_id,
            ]:
                raise ProjectSkillError("skill_storage_unavailable", 503)
            storage = self._storage_factory()
            package = replace_manifest(
                read_verified_package(storage, original), request.content
            )
            if package.name != original.name:
                raise ProjectSkillError("skill_name_immutable", 422)
            stored = storage.put(
                access.scope.unit_id, access.scope.project_id, skill_id, package
            )
            with self._session_factory() as session, session.begin():
                access = require_skill_access(session, context, "skill.manage")
                repository = SkillRepository(session)
                repository.lock_skill(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                current = repository.get_draft(
                    access.scope, skill_id, owner_ids=access.owner_ids
                )
                if current is None or snapshot_draft(current) != original:
                    raise ProjectSkillError("skill_revision_conflict", 409)
                draft = repository.save_draft(
                    access.scope,
                    skill_id,
                    expected_revision=request.expected_revision,
                    package=package,
                    stored=stored,
                )
                self._record_change(
                    session,
                    context,
                    skill_id,
                    "skill.draft.save",
                    revision=draft.revision,
                    digest=stored.package_digest,
                )
                result = SkillDraftInfo(**self._draft_fields(draft))
            return result
        except (
            SkillPackageError,
            SkillPackageStorageError,
            SkillRevisionConflict,
            SkillResourceNotFound,
            IntegrityError,
        ) as error:
            self._raise_known(error)
            raise

    @staticmethod
    def _require_create_owner(context: RequestContext, access: SkillAccess) -> None:
        if access.owner_ids is not None and context.user_id not in access.owner_ids:
            raise ProjectSkillError("skill_permission_denied", 403)

    @staticmethod
    def _raise_known(error: Exception) -> None:
        if isinstance(error, SkillPackageError):
            raise ProjectSkillError("skill_package_invalid", 422) from error
        if isinstance(error, SkillPackageStorageError):
            raise ProjectSkillError("skill_storage_unavailable", 503) from error
        if isinstance(error, SkillRevisionConflict):
            raise ProjectSkillError("skill_revision_conflict", 409) from error
        if isinstance(error, SkillIdempotencyConflict):
            raise ProjectSkillError("skill_idempotency_conflict", 409) from error
        if isinstance(error, SkillResourceNotFound):
            raise ProjectSkillError("skill_not_found", 404) from error
        if isinstance(error, IntegrityError):
            constraint = getattr(
                getattr(error.orig, "diag", None), "constraint_name", None
            )
            if constraint == "uq_skills_scope_name" or str(error.orig) == (
                "UNIQUE constraint failed: skills.unit_id, skills.project_id, skills.name"
            ):
                raise ProjectSkillError("skill_name_conflict", 409) from error

    @staticmethod
    def _draft_fields(row: SkillDraft) -> dict[str, object]:
        return {
            field: getattr(row, field)
            for field in (
                "skill_id",
                "name",
                "description",
                "display_version",
                "revision",
                "content",
                "files",
                "package_digest",
                "updated_at",
            )
        }

    def _record_change(
        self,
        session: Session,
        context: RequestContext,
        skill_id: str,
        action: str,
        *,
        revision: int,
        digest: str,
        version_id: str | None = None,
        trace_id: str | None = None,
        enabled: bool | None = None,
        request_id: ManagementAuditIdentity | None = None,
    ) -> None:
        metadata: dict[str, object] = {"revision": revision, "digest": digest}
        if version_id is not None:
            metadata["version_id"] = version_id
        if enabled is not None:
            metadata["enabled"] = enabled
        event_id = management_event_id(request_id)
        key = (
            f"skill:{skill_id}:publish:{version_id}"
            if version_id is not None
            else f"skill:{skill_id}:{action}:{event_id}"
        )
        authorization = context.authorization_context
        (self._audit_recorder or AuditRecorder()).record(
            session,
            AuditRecordRequest(
                unit_id=context.unit_id,
                project_id=context.project_id,
                user_id=context.user_id,
                actor_roles=context.role_codes,
                auth_method=authorization.auth_method if authorization else None,
                category="management",
                source="system",
                resource_type="skill",
                resource_id=skill_id,
                event_scope="project",
                authorization_scope="project",
                status="succeeded",
                risk_level="medium",
                action=action,
                occurred_at=datetime.now(UTC),
                trace_id=trace_id or management_trace_id(request_id) or str(uuid4()),
                idempotency_key=key,
                metadata=metadata,
                allowed_metadata_keys=frozenset(
                    {"revision", "digest", "version_id", "enabled"}
                ),
            ),
        )

    def list(
        self,
        context: RequestContext,
        *,
        offset: int = 0,
        limit: int = 20,
        q: str | None = None,
    ) -> SkillPage:
        with self._session_factory() as session:
            access = require_skill_access(session, context, "skill.read")
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
            access = require_skill_access(session, context, "skill.read")
            row = SkillRepository(session).get_summary(
                access.scope, skill_id, owner_ids=access.owner_ids
            )
            return SkillSummary(**dict(self._found(row)))

    def get_draft(self, context: RequestContext, skill_id: str) -> SkillDraftInfo:
        with self._session_factory() as session:
            access = require_skill_access(session, context, "skill.read")
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
            access = require_skill_access(session, context, "skill.read")
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
            access = require_skill_access(session, context, "skill.read")
            repository = SkillRepository(session)
            version = repository.get_version(
                access.scope,
                skill_id,
                version_id,
                owner_ids=access.owner_ids,
            )
            if version is None:
                raise ProjectSkillError("skill_not_found", 404)
            skill = repository.get(
                access.scope, skill_id, owner_ids=access.owner_ids
            )
            return SkillVersionInfo(
                id=version.id,
                skill_id=version.skill_id,
                version=version.version,
                source_revision=version.source_revision,
                name=version.name,
                description=version.description,
                display_version=version.display_version,
                enabled=skill.enabled,
                package_digest=version.package_digest,
                published_by=version.published_by,
                published_at=version.published_at,
                content=version.content,
                files=version.files,
            )

    @staticmethod
    def _found(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if row is None:
            raise ProjectSkillError("skill_not_found", 404)
        return row
