from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.audit.recorder import AuditRecordRequest, AuditRecorder
from app.core.request_context import RequestContext

from .models import Team, TeamVersion
from .repository import (
    TeamDefinitionValidationError,
    TeamDraftConflictError,
    TeamNotFoundError,
    TeamRepository,
)
from .schemas import (
    ResolvedTeamRunActor,
    TeamCreateRequest,
    TeamDraftUpdate,
    TeamMetadataUpdate,
    TeamSummary,
    TeamVersionInfo,
)


class TeamPermissionError(PermissionError):
    pass


class TeamUnavailableError(LookupError):
    pass


def _digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class TeamService:
    def __init__(self, session: Session, *, audit_recorder: AuditRecorder | None = None):
        self.session = session
        self.repository = TeamRepository(session)
        self.audit_recorder = audit_recorder or AuditRecorder()

    @staticmethod
    def _require(context: RequestContext, permission: str) -> None:
        if permission == "collaboration.read":
            return
        if context.role != "admin":
            raise TeamPermissionError(f"{permission} is required")

    @staticmethod
    def _summary(team: Team) -> TeamSummary:
        published = next((v for v in team.versions if v.id == team.published_version_id), None)
        members = list(published.members) if published else []
        supervisor = next((m for m in members if m.role == "supervisor"), None)
        return TeamSummary(
            id=team.id, unit_id=team.unit_id, project_id=team.project_id,
            name=team.name, description=team.description, enabled=team.enabled,
            draft_revision=team.draft_revision,
            published_version=published.version if published else None,
            member_count=len([m for m in members if m.role == "member"]),
            supervisor=None if supervisor is None else {
                "agent_id": supervisor.agent_id, "role": supervisor.role,
                "responsibility": supervisor.responsibility,
                "agent_definition_digest": supervisor.agent_definition_digest,
            },
            updated_at=team.updated_at,
        )

    def list(self, context: RequestContext) -> list[TeamSummary]:
        self._require(context, "collaboration.read")
        return [self._summary(team) for team in self.repository.list_scoped(context.unit_id, context.project_id)]

    def get(self, context: RequestContext, team_id: str) -> TeamSummary:
        self._require(context, "collaboration.read")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        return self._summary(team)

    def create(self, context: RequestContext, request: TeamCreateRequest) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.create(
            unit_id=context.unit_id, project_id=context.project_id,
            name=request.name, description=request.description,
            created_by=context.user_id,
        )
        self.session.commit()
        return self._summary(team)

    def update(self, context: RequestContext, team_id: str, request: TeamMetadataUpdate) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        team.name, team.description, team.updated_by = request.name, request.description, context.user_id
        self.session.commit()
        return self._summary(team)

    def save_draft(self, context: RequestContext, team_id: str, request: TeamDraftUpdate) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        self.repository.save_draft(team_id, expected_revision=request.revision,
                                   definition=request.draft.model_dump(), updated_by=context.user_id)
        self.session.commit()
        return self._summary(team)

    def publish(self, context: RequestContext, team_id: str) -> TeamVersionInfo:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        draft = self.repository.get_version(team.id, 0)
        if draft is None:
            raise TeamDefinitionValidationError("Team has no draft definition")
        published = self.repository.publish(team.id, expected_revision=team.draft_revision,
                                             definition_digest=_digest(draft.definition), published_by=context.user_id)
        self.session.commit()
        return self._version_info(published)

    def set_enabled(self, context: RequestContext, team_id: str, enabled: bool) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        if enabled and team.published_version_id is None:
            raise TeamUnavailableError("Team must be published before enabling")
        team.enabled, team.updated_by = enabled, context.user_id
        self.session.commit()
        return self._summary(team)

    def resolve_for_run(self, context: RequestContext, team_id: str) -> ResolvedTeamRunActor:
        self._require(context, "collaboration.run")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None or not team.enabled or not team.published_version_id:
            raise TeamUnavailableError("team_unavailable")
        version = self.repository.get_version_by_id(team.published_version_id)
        if version is None or version.status != "published":
            raise TeamUnavailableError("team_unavailable")
        return ResolvedTeamRunActor(team_id=team.id, version_id=version.id,
                                    definition_digest=version.definition_digest or _digest(version.definition))

    @staticmethod
    def _version_info(version: TeamVersion) -> TeamVersionInfo:
        return TeamVersionInfo(
            id=version.id, team_id=version.team_id, version=version.version,
            status=version.status, definition=version.definition,
            definition_digest=version.definition_digest,
            members=[{"agent_id": m.agent_id, "role": m.role, "responsibility": m.responsibility,
                       "agent_definition_digest": m.agent_definition_digest} for m in version.members],
            max_steps=version.max_steps, max_parallel_members=version.max_parallel_members,
            timeout_seconds=version.timeout_seconds, failure_strategy=version.failure_strategy,
            published_by=version.published_by, published_at=version.published_at,
        )
