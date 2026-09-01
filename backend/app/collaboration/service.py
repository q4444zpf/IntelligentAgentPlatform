from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.service import AgentNotFoundError, AgentService
from app.audit.recorder import AuditRecordRequest, AuditRecorder
from app.core.request_context import RequestContext
from app.identity.authorization import AuthorizationService
from app.identity.schemas import ResourceScope
from app.skills.service import SkillNotFoundError, SkillValidationError
from app.tools.service import ToolNotFoundError, ToolValidationError

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
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TeamService:
    def __init__(
        self,
        session: Session,
        *,
        audit_recorder: AuditRecorder | None = None,
        agent_service: AgentService | None = None,
        max_snapshot_bytes: int | None = None,
    ):
        self.session = session
        self.repository = TeamRepository(session)
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.agent_service = agent_service or AgentService()
        self.max_snapshot_bytes = max_snapshot_bytes or int(
            os.getenv("IAP_RUNNER_SNAPSHOT_MAX_BYTES", "1048576")
        )

    @staticmethod
    def _require(context: RequestContext, permission: str) -> None:
        authorization = context.authorization_context
        if authorization is None or authorization.current_project_id != context.project_id:
            raise TeamPermissionError(f"{permission} is required")
        allowed = AuthorizationService().allows(
            authorization,
            permission,
            ResourceScope(context.unit_id, context.project_id, context.user_id),
        )
        if not allowed:
            raise TeamPermissionError(f"{permission} is required")

    def _commit_mutation(
        self,
        context: RequestContext,
        team: Team,
        *,
        action: str,
        metadata: dict[str, str | bool | int] | None = None,
    ) -> None:
        try:
            self.audit_recorder.record(
                self.session,
                AuditRecordRequest(
                    unit_id=context.unit_id,
                    project_id=context.project_id,
                    user_id=context.user_id,
                    actor_roles=context.role_codes,
                    authorization_scope="project",
                    event_scope="project",
                    category="management",
                    source="agent",
                    action=action,
                    status="succeeded",
                    risk_level="medium",
                    resource_type="team",
                    resource_id=team.id,
                    resource_name=team.name,
                    metadata=metadata or {},
                    allowed_metadata_keys=frozenset((metadata or {}).keys()),
                    idempotency_key=f"team:{team.id}:{action}:{uuid4()}",
                    occurred_at=datetime.now(UTC),
                ),
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

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

    def list_versions(self, context: RequestContext, team_id: str) -> list[TeamVersionInfo]:
        self._require(context, "collaboration.read")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        return [self._version_info(version) for version in self.repository.list_published_versions(team.id)]

    def get_version(self, context: RequestContext, team_id: str, version: int) -> TeamVersionInfo:
        self._require(context, "collaboration.manage" if version == 0 else "collaboration.read")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        stored = self.repository.get_version(team.id, version)
        if stored is None:
            raise TeamNotFoundError(f"{team_id}:{version}")
        return self._version_info(stored)

    def create(self, context: RequestContext, request: TeamCreateRequest) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.create(
            unit_id=context.unit_id, project_id=context.project_id,
            name=request.name, description=request.description,
            created_by=context.user_id,
        )
        self._commit_mutation(context, team, action="resource.created")
        return self._summary(team)

    def update(self, context: RequestContext, team_id: str, request: TeamMetadataUpdate) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        team.name, team.description, team.updated_by = request.name, request.description, context.user_id
        self._commit_mutation(context, team, action="resource.updated")
        return self._summary(team)

    def save_draft(self, context: RequestContext, team_id: str, request: TeamDraftUpdate) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        self.repository.save_draft(team_id, expected_revision=request.revision,
                                   definition=request.draft.model_dump(), updated_by=context.user_id)
        self._commit_mutation(context, team, action="resource.updated")
        return self._summary(team)

    def publish(self, context: RequestContext, team_id: str) -> TeamVersionInfo:
        self._require(context, "collaboration.manage")
        try:
            team = self.repository.get_scoped(
                context.unit_id, context.project_id, team_id
            )
            if team is None:
                raise TeamNotFoundError(team_id)
            draft = self.repository.get_version(team.id, 0)
            if draft is None:
                raise TeamDefinitionValidationError("Team has no draft definition")
            trusted_definition = self._trusted_definition(
                context, team, draft.definition
            )
            if len(self._canonical_bytes(trusted_definition)) > self.max_snapshot_bytes:
                raise TeamDefinitionValidationError(
                    f"Team definition exceeds {self.max_snapshot_bytes} bytes"
                )
            published = self.repository.publish(
                team.id,
                expected_revision=team.draft_revision,
                definition=trusted_definition,
                definition_digest=_digest(trusted_definition),
                published_by=context.user_id,
            )
            self._commit_mutation(
                context,
                team,
                action="resource.published",
                metadata={"version_id": published.id},
            )
        except Exception:
            self.session.rollback()
            raise
        return self._version_info(published)

    @staticmethod
    def _canonical_bytes(value: dict) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def _trusted_definition(
        self, context: RequestContext, team: Team, draft: dict
    ) -> dict:
        editable = self.repository._normalize_definition(draft)
        members = [editable["supervisor"], *editable["members"]]
        captured: dict[str, dict] = {}
        agent_tool_ids: dict[str, set[str]] = {}
        agent_skill_names: dict[str, set[str]] = {}
        agent_knowledge_ids: dict[str, set[str]] = {}

        for member in members:
            agent_id = member["agent_id"]
            try:
                agent = self.agent_service.get(agent_id)
            except (AgentNotFoundError, KeyError) as error:
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' is unavailable"
                ) from error
            if not getattr(agent, "enabled", False):
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' is disabled"
                )
            agent_unit_id = getattr(agent, "unit_id", None)
            agent_project_id = getattr(agent, "project_id", None)
            if agent_unit_id not in (None, "", context.unit_id) or agent_project_id not in (
                None,
                "",
                context.project_id,
            ):
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' is outside the current project"
                )

            tool_ids = list(agent.tool_ids)
            try:
                tools = self.agent_service.tool_service.resolve_bindable(tool_ids)
            except (ToolNotFoundError, ToolValidationError, ValueError) as error:
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' has unavailable Tool binding: {error}"
                ) from error

            skill_names = list(agent.skill_names)
            for skill_name in skill_names:
                try:
                    skill = self.agent_service.skill_service.get(skill_name)
                except (SkillNotFoundError, SkillValidationError, ValueError) as error:
                    raise TeamDefinitionValidationError(
                        f"Agent '{agent_id}' has unavailable Skill '{skill_name}'"
                    ) from error
                if not getattr(skill, "enabled", True):
                    raise TeamDefinitionValidationError(
                        f"Agent '{agent_id}' has unavailable Skill '{skill_name}'"
                    )

            knowledge_source_ids = list(
                getattr(agent, "knowledge_source_ids", ())
            )
            definition = {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "runtime_form": agent.runtime_form,
                "language": agent.language,
                "provider_id": agent.provider_id,
                "model": agent.model,
                "system_prompt": agent.system_prompt,
                "context_prompt": agent.context_prompt,
                "approval_policy": agent.approval_policy,
                "skill_names": skill_names,
                "tool_ids": tool_ids,
                "knowledge_source_ids": knowledge_source_ids,
                "tools": [
                    {
                        "tool_id": tool.tool_id,
                        "version": tool.version,
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": deepcopy(tool.input_schema),
                        "published": tool.published,
                        "enabled": tool.enabled,
                        "source_available": tool.source_available,
                    }
                    for tool in tools
                ],
            }
            captured[agent_id] = definition
            agent_tool_ids[agent_id] = set(tool_ids)
            agent_skill_names[agent_id] = set(skill_names)
            agent_knowledge_ids[agent_id] = set(knowledge_source_ids)

        self._validate_team_whitelists(
            editable,
            agent_tool_ids=agent_tool_ids,
            agent_skill_names=agent_skill_names,
            agent_knowledge_ids=agent_knowledge_ids,
        )
        self._validate_current_team_capabilities(editable)

        trusted_members = []
        for member in members:
            trusted_member = deepcopy(member)
            definition = captured[member["agent_id"]]
            trusted_member["agent_definition"] = definition
            trusted_member["agent_definition_digest"] = _digest(definition)
            trusted_members.append(trusted_member)

        return {
            "name": team.name,
            "description": team.description,
            "supervisor": trusted_members[0],
            "members": trusted_members[1:],
            "tool_ids": deepcopy(editable["tool_ids"]),
            "skill_names": deepcopy(editable["skill_names"]),
            "knowledge_source_ids": deepcopy(editable["knowledge_source_ids"]),
            "max_steps": editable["max_steps"],
            "max_parallel_members": editable["max_parallel_members"],
            "timeout_seconds": editable["timeout_seconds"],
            "failure_strategy": editable["failure_strategy"],
            "approval_policy_id": editable["approval_policy_id"],
        }

    def _validate_current_team_capabilities(self, definition: dict) -> None:
        try:
            self.agent_service.tool_service.resolve_bindable(definition["tool_ids"])
        except (ToolNotFoundError, ToolValidationError, ValueError) as error:
            raise TeamDefinitionValidationError(
                f"Team has unavailable Tool whitelist value: {error}"
            ) from error
        for skill_name in definition["skill_names"]:
            try:
                skill = self.agent_service.skill_service.get(skill_name)
            except (SkillNotFoundError, SkillValidationError, ValueError) as error:
                raise TeamDefinitionValidationError(
                    f"Team has unavailable Skill '{skill_name}'"
                ) from error
            if not getattr(skill, "enabled", True):
                raise TeamDefinitionValidationError(
                    f"Team has unavailable Skill '{skill_name}'"
                )

    @staticmethod
    def _validate_team_whitelists(
        definition: dict,
        *,
        agent_tool_ids: dict[str, set[str]],
        agent_skill_names: dict[str, set[str]],
        agent_knowledge_ids: dict[str, set[str]],
    ) -> None:
        team_tools = set(definition["tool_ids"])
        team_skills = set(definition["skill_names"])
        team_knowledge = set(definition["knowledge_source_ids"])
        unions = (
            ("Tool", team_tools, set().union(*agent_tool_ids.values())),
            ("Skill", team_skills, set().union(*agent_skill_names.values())),
            ("knowledge source", team_knowledge, set().union(*agent_knowledge_ids.values())),
        )
        for label, requested, available in unions:
            outside = requested - available
            if outside:
                raise TeamDefinitionValidationError(
                    f"Team {label} whitelist is outside Agent bindings: {sorted(outside)[0]}"
                )

        for member in [definition["supervisor"], *definition["members"]]:
            agent_id = member["agent_id"]
            checks = (
                ("Tool", set(member["tool_ids"]), team_tools, agent_tool_ids[agent_id]),
                (
                    "Skill",
                    set(member["skill_names"]),
                    team_skills,
                    agent_skill_names[agent_id],
                ),
                (
                    "knowledge source",
                    set(member["knowledge_source_ids"]),
                    team_knowledge,
                    agent_knowledge_ids[agent_id],
                ),
            )
            for label, requested, team_allowed, agent_allowed in checks:
                outside = requested - (team_allowed & agent_allowed)
                if outside:
                    raise TeamDefinitionValidationError(
                        f"Agent '{agent_id}' {label} whitelist is invalid: {sorted(outside)[0]}"
                    )

    def set_enabled(self, context: RequestContext, team_id: str, enabled: bool) -> TeamSummary:
        self._require(context, "collaboration.manage")
        team = self.repository.get_scoped(context.unit_id, context.project_id, team_id)
        if team is None:
            raise TeamNotFoundError(team_id)
        if enabled and team.published_version_id is None:
            raise TeamUnavailableError("Team must be published before enabling")
        team.enabled, team.updated_by = enabled, context.user_id
        self._commit_mutation(
            context,
            team,
            action="resource.enabled" if enabled else "resource.disabled",
            metadata={"enabled": enabled},
        )
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
