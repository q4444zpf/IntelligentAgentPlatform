from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from .models import Skill, SkillDraft, SkillVersion
from .package import MAX_ZIP_BYTES, ValidatedSkillPackage
from .package_storage import SHA256_HEX, STORED_OBJECT_KEY, StoredSkillPackage


@dataclass(frozen=True)
class SkillScope:
    unit_id: str
    project_id: str


class SkillRevisionConflict(ValueError):
    pass


class SkillIdempotencyConflict(ValueError):
    pass


class SkillResourceNotFound(LookupError):
    pass


def _snapshot(
    scope: SkillScope,
    skill_id: str,
    package: ValidatedSkillPackage,
    stored: StoredSkillPackage,
) -> dict[str, Any]:
    if str(UUID(skill_id)) != skill_id:
        raise ValueError("Skill ID must be a canonical UUID")
    if (
        not STORED_OBJECT_KEY.fullmatch(stored.object_key)
        or stored.object_key.split("/")[:3] != [scope.unit_id, scope.project_id, skill_id]
        or stored.package_digest != package.digest
        or not SHA256_HEX.fullmatch(stored.package_digest)
        or not SHA256_HEX.fullmatch(stored.archive_sha256)
        or not 0 < stored.size_bytes <= MAX_ZIP_BYTES
    ):
        raise ValueError("Stored package must match the skill scope and package digest")
    return {
        "name": package.name,
        "description": package.description,
        "display_version": package.display_version,
        "content": package.content,
        "files": [
            {"path": item.path, "size": len(item.data), "sha256": item.sha256}
            for item in package.files
        ],
        "package_digest": stored.package_digest,
        "object_key": stored.object_key,
        "archive_sha256": stored.archive_sha256,
        "size_bytes": stored.size_bytes,
    }


class SkillRepository:
    """Scoped persistence; callers authorize requests and commit or roll back the session.

    Publishing assumes the service has reverified the stored archive and parsed body.
    PostgreSQL's default READ COMMITTED isolation permits replay after a lock wait.
    """

    def __init__(self, session: Session):
        self._session = session

    def create(
        self,
        scope: SkillScope,
        *,
        skill_id: str,
        name: str,
        created_by: str,
        package: ValidatedSkillPackage,
        stored: StoredSkillPackage,
    ) -> Skill:
        snapshot = _snapshot(scope, skill_id, package, stored)
        skill = Skill(
            id=skill_id, unit_id=scope.unit_id, project_id=scope.project_id,
            name=name, created_by=created_by,
        )
        self._session.add(skill)
        self._session.flush()
        self._session.add(SkillDraft(skill_id=skill_id, revision=1, **snapshot))
        self._session.flush()
        return skill

    def get(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        owner_ids: frozenset[str] | None = None,
    ) -> Skill | None:
        return self._session.scalar(
            self._scoped(scope, owner_ids=owner_ids).where(Skill.id == skill_id)
        )

    def name_exists(self, scope: SkillScope, name: str) -> bool:
        return self._session.scalar(
            select(Skill.id).where(
                Skill.unit_id == scope.unit_id,
                Skill.project_id == scope.project_id,
                Skill.name == name,
            )
        ) is not None

    def list(self, scope: SkillScope, *, offset: int = 0, limit: int = 20) -> list[Skill]:
        if offset < 0 or limit < 0:
            raise ValueError("Pagination must be nonnegative")
        statement = self._scoped(scope).order_by(Skill.created_at, Skill.id).offset(offset).limit(limit)
        return list(self._session.scalars(statement))

    def list_summaries(
        self,
        scope: SkillScope,
        *,
        owner_ids: frozenset[str] | None = None,
        offset: int = 0,
        limit: int = 20,
        q: str | None = None,
    ) -> tuple[list[Mapping[str, Any]], int]:
        if offset < 0 or limit < 0:
            raise ValueError("Pagination must be nonnegative")
        conditions = self._conditions(scope, owner_ids=owner_ids, q=q)
        total = (
            self._session.scalar(
                select(func.count())
                .select_from(Skill)
                .join(SkillDraft, SkillDraft.skill_id == Skill.id)
                .where(*conditions)
            )
            or 0
        )
        statement = (
            self._summary_statement(conditions)
            .order_by(Skill.created_at, Skill.id)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.execute(statement).mappings()), int(total)

    def get_summary(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        owner_ids: frozenset[str] | None = None,
    ) -> Mapping[str, Any] | None:
        conditions = [
            *self._conditions(scope, owner_ids=owner_ids),
            Skill.id == skill_id,
        ]
        statement = self._summary_statement(conditions)
        return self._session.execute(statement).mappings().one_or_none()

    def get_draft(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        owner_ids: frozenset[str] | None = None,
    ) -> SkillDraft | None:
        return self._session.scalar(
            select(SkillDraft)
            .join(Skill, Skill.id == SkillDraft.skill_id)
            .where(*self._conditions(scope, owner_ids=owner_ids), Skill.id == skill_id)
        )

    def list_version_summaries(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        owner_ids: frozenset[str] | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Mapping[str, Any]], int]:
        if offset < 0 or limit < 0:
            raise ValueError("Pagination must be nonnegative")
        conditions = [
            *self._conditions(scope, owner_ids=owner_ids),
            Skill.id == skill_id,
        ]
        total = (
            self._session.scalar(
                select(func.count())
                .select_from(SkillVersion)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .where(*conditions)
            )
            or 0
        )
        statement = (
            select(
                SkillVersion.id,
                SkillVersion.skill_id,
                SkillVersion.version,
                SkillVersion.source_revision,
                SkillVersion.name,
                SkillVersion.description,
                SkillVersion.display_version,
                SkillVersion.package_digest,
                SkillVersion.published_by,
                SkillVersion.published_at,
            )
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(*conditions)
            .order_by(SkillVersion.version.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.execute(statement).mappings()), int(total)

    def save_draft(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        expected_revision: int,
        package: ValidatedSkillPackage,
        stored: StoredSkillPackage,
    ) -> SkillDraft:
        self._lock(scope, skill_id)
        values = _snapshot(scope, skill_id, package, stored)
        return self._advance_draft(skill_id, expected_revision, values)

    def publish(
        self,
        scope: SkillScope,
        skill_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        published_by: str,
    ) -> SkillVersion:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("An idempotency key of at most 128 characters is required")
        if self.get(scope, skill_id) is None:
            raise SkillResourceNotFound("Skill not found in scope")
        request = {
            "operation": "publish",
            "skill_id": skill_id,
            "expected_revision": expected_revision,
            "published_by": published_by,
        }
        request_digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = self._replay(skill_id, idempotency_key, request_digest)
        if existing is not None:
            return existing
        skill = self._lock(scope, skill_id)
        # A competing publisher can commit while this transaction waits for the lock.
        existing = self._replay(skill_id, idempotency_key, request_digest)
        if existing is not None:
            return existing
        draft = self._advance_draft(skill_id, expected_revision, {})
        last_version = self._session.scalar(
            select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id)
        ) or 0
        snapshot_fields = (
            "name", "description", "display_version", "content", "files", "package_digest",
            "object_key", "archive_sha256", "size_bytes",
        )
        version = SkillVersion(
            skill_id=skill_id, version=last_version + 1, source_revision=expected_revision,
            idempotency_key=idempotency_key, request_digest=request_digest,
            published_by=published_by,
            **{field: deepcopy(getattr(draft, field)) for field in snapshot_fields},
        )
        self._session.add(version)
        self._session.flush()
        skill.published_version_id = version.id
        self._session.flush()
        return version

    def get_version(
        self,
        scope: SkillScope,
        skill_id: str,
        version_id: str,
        *,
        owner_ids: frozenset[str] | None = None,
    ) -> SkillVersion | None:
        return self._session.scalar(
            select(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(
                *self._conditions(scope, owner_ids=owner_ids),
                Skill.id == skill_id,
                SkillVersion.id == version_id,
            )
        )

    def _conditions(
        self,
        scope: SkillScope,
        *,
        owner_ids: frozenset[str] | None = None,
        q: str | None = None,
    ) -> list[Any]:
        conditions = [
            Skill.unit_id == scope.unit_id,
            Skill.project_id == scope.project_id,
        ]
        if owner_ids is not None:
            conditions.append(Skill.created_by.in_(owner_ids))
        if q is not None:
            conditions.append(Skill.name.contains(q, autoescape=True))
        return conditions

    @staticmethod
    def _summary_statement(conditions: list[Any]):
        updated_at = case(
            (Skill.updated_at >= SkillDraft.updated_at, Skill.updated_at),
            else_=SkillDraft.updated_at,
        ).label("updated_at")
        return (
            select(
                Skill.id,
                Skill.name,
                SkillDraft.description,
                SkillDraft.display_version,
                SkillDraft.revision.label("draft_revision"),
                Skill.published_version_id,
                Skill.created_at,
                updated_at,
            )
            .join(SkillDraft, SkillDraft.skill_id == Skill.id)
            .where(*conditions)
        )

    def _scoped(self, scope: SkillScope, *, owner_ids: frozenset[str] | None = None):
        return select(Skill).where(*self._conditions(scope, owner_ids=owner_ids))

    def _lock(self, scope: SkillScope, skill_id: str) -> Skill:
        return self.lock_skill(scope, skill_id)

    def lock_skill(
        self, scope: SkillScope, skill_id: str, *, owner_ids: frozenset[str] | None = None,
    ) -> Skill:
        skill = self._session.scalar(
            self._scoped(scope, owner_ids=owner_ids).where(Skill.id == skill_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if skill is None:
            raise SkillResourceNotFound("Skill not found in scope")
        return skill

    def _advance_draft(
        self, skill_id: str, expected_revision: int, values: dict[str, Any]
    ) -> SkillDraft:
        draft = self._session.scalar(
            update(SkillDraft).where(
                SkillDraft.skill_id == skill_id, SkillDraft.revision == expected_revision,
            ).values(**values, revision=SkillDraft.revision + 1).returning(SkillDraft)
            .execution_options(populate_existing=True)
        )
        if draft is None:
            raise SkillRevisionConflict("Skill draft revision has changed")
        return draft

    def _replay(self, skill_id: str, idempotency_key: str, request_digest: str) -> SkillVersion | None:
        existing = self._session.scalar(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill_id, SkillVersion.idempotency_key == idempotency_key,
            )
        )
        if existing is not None and existing.request_digest != request_digest:
            raise SkillIdempotencyConflict("Idempotency key was already used for another request")
        return existing
