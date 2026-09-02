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
from app.model_providers.service import ProviderService
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
        provider_service: ProviderService | None = None,
        max_snapshot_bytes: int | None = None,
        max_team_steps: int | None = None,
        max_parallel_members: int | None = None,
        max_subagents: int | None = None,
        max_team_members: int | None = None,
    ):
        self.session = session
        self.repository = TeamRepository(session)
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.agent_service = agent_service or AgentService()
        self.provider_service = provider_service or ProviderService()
        self.max_snapshot_bytes = max_snapshot_bytes or int(
            os.getenv("IAP_RUNNER_SNAPSHOT_MAX_BYTES", "1048576")
        )
        self.max_team_steps = max_team_steps or int(
            os.getenv("IAP_RUNNER_MAX_TEAM_STEPS", "128")
        )
        self.max_subagents = (
            max_subagents
            if max_subagents is not None
            else int(os.getenv("IAP_RUNNER_MAX_SUBAGENTS", "4"))
        )
        self.max_parallel_members = max_parallel_members or int(
            os.getenv(
                "IAP_RUNNER_MAX_PARALLEL_MEMBERS",
                str(max(1, self.max_subagents)),
            )
        )
        self.max_team_members = max_team_members or int(
            os.getenv("IAP_RUNNER_MAX_TEAM_MEMBERS", "32")
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

    def list(
        self,
        context: RequestContext,
        *,
        enabled: bool | None = None,
        published: bool | None = None,
    ) -> list[TeamSummary]:
        self._require(context, "collaboration.read")
        teams = self.repository.list_scoped(context.unit_id, context.project_id)
        if enabled is not None:
            teams = [team for team in teams if team.enabled is enabled]
        if published is not None:
            teams = [team for team in teams if (team.published_version_id is not None) is published]
        return [self._summary(team) for team in teams]

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
            team, draft = self.repository.lock_publication_draft_scoped(
                context.unit_id, context.project_id, team_id
            )
            revision = team.draft_revision
            trusted_definition = self._trusted_definition(
                context, team, draft.definition
            )
            if len(self._canonical_bytes(trusted_definition)) > self.max_snapshot_bytes:
                raise TeamDefinitionValidationError(
                    f"Team definition exceeds {self.max_snapshot_bytes} bytes"
                )
            published = self.repository.publish(
                team.id,
                expected_revision=revision,
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

        self._validate_runner_ceilings(editable)

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
            if not self._agent_is_available(agent, context):
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' is outside the current project"
                )

            provider_id, model_id = self._resolve_model(agent_id, agent)

            tool_ids = list(agent.tool_ids)
            try:
                tools = self.agent_service.tool_service.resolve_bindable(tool_ids)
            except (ToolNotFoundError, ToolValidationError, ValueError) as error:
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' has unavailable Tool binding: {error}"
                ) from error

            skill_names = list(agent.skill_names)
            skills = []
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
                skills.append(self._skill_definition(skill))

            knowledge_source_ids = list(agent.knowledge_source_ids)
            try:
                knowledge_sources = (
                    self.agent_service.tool_service.resolve_knowledge_sources(
                        knowledge_source_ids
                    )
                )
            except (ToolNotFoundError, ToolValidationError, ValueError) as error:
                raise TeamDefinitionValidationError(
                    f"Agent '{agent_id}' has unavailable knowledge source: {error}"
                ) from error
            definition = {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "runtime_form": agent.runtime_form,
                "language": agent.language,
                "provider_id": provider_id,
                "model": model_id,
                "system_prompt": agent.system_prompt,
                "context_prompt": agent.context_prompt,
                "approval_policy": agent.approval_policy,
                "enabled": True,
                "availability_scope": agent.availability_scope,
                "unit_id": agent.unit_id,
                "project_id": agent.project_id,
                "allowed_project_ids": list(agent.allowed_project_ids),
                "skill_names": skill_names,
                "skills": skills,
                "tool_ids": tool_ids,
                "knowledge_source_ids": knowledge_source_ids,
                "tools": [self._tool_definition(tool) for tool in tools],
                "knowledge_sources": [
                    self._tool_definition(source) for source in knowledge_sources
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
        try:
            self.agent_service.tool_service.resolve_knowledge_sources(
                definition["knowledge_source_ids"]
            )
        except (ToolNotFoundError, ToolValidationError, ValueError) as error:
            raise TeamDefinitionValidationError(
                f"Team has unavailable knowledge source: {error}"
            ) from error

    @staticmethod
    def _agent_is_available(agent, context: RequestContext) -> bool:
        scope = getattr(agent, "availability_scope", None)
        allowed = getattr(agent, "allowed_project_ids", None)
        if scope == "project":
            return (
                getattr(agent, "unit_id", None) == context.unit_id
                and getattr(agent, "project_id", None) == context.project_id
                and allowed == []
            )
        if scope == "common":
            return (
                getattr(agent, "unit_id", None) is None
                and getattr(agent, "project_id", None) is None
                and isinstance(allowed, list)
                and bool(allowed)
                and ("*" in allowed or context.project_id in allowed)
                and ("*" not in allowed or allowed == ["*"])
            )
        return False

    def _resolve_model(self, agent_id: str, agent) -> tuple[str, str]:
        provider_id = agent.provider_id
        model_id = agent.model
        if not provider_id or not model_id:
            active = self.provider_service.get_active()
            provider_id, model_id = active.provider_id, active.model
        try:
            provider = self.provider_service.get(provider_id)
        except (KeyError, ValueError) as error:
            raise TeamDefinitionValidationError(
                f"Agent '{agent_id}' Provider is unavailable"
            ) from error
        model = next((item for item in provider.models if item.id == model_id), None)
        if not provider.configured or not provider.enabled:
            raise TeamDefinitionValidationError(
                f"Agent '{agent_id}' Provider is unavailable"
            )
        if model is None or not model.enabled:
            raise TeamDefinitionValidationError(
                f"Agent '{agent_id}' model is unavailable"
            )
        return provider_id, model_id

    @staticmethod
    def _skill_definition(skill) -> dict:
        fields = (
            "name",
            "description",
            "version",
            "content",
            "source",
            "enabled",
            "tags",
            "metadata",
            "file_count",
            "updated_at",
        )
        if hasattr(skill, "model_dump"):
            raw = skill.model_dump(mode="json")
            return {field: deepcopy(raw[field]) for field in fields}
        result = {field: deepcopy(getattr(skill, field)) for field in fields}
        updated_at = result["updated_at"]
        if isinstance(updated_at, datetime):
            result["updated_at"] = updated_at.isoformat().replace("+00:00", "Z")
        return result

    @staticmethod
    def _tool_definition(tool) -> dict:
        return {
            "tool_id": tool.tool_id,
            "version": tool.version,
            "name": tool.name,
            "description": tool.description,
            "source": tool.source,
            "risk_level": tool.risk_level,
            "input_schema": deepcopy(tool.input_schema),
            "output_schema": deepcopy(tool.output_schema),
            "source_resource_id": tool.source_resource_id,
            "source_capability_id": tool.source_capability_id,
            "source_available": tool.source_available,
            "requires_approval": tool.requires_approval,
            "published": tool.published,
            "enabled": tool.enabled,
        }

    def _validate_runner_ceilings(self, definition: dict) -> None:
        member_count = 1 + len(definition["members"])
        subagent_count = len(definition["members"])
        if member_count > self.max_team_members:
            raise TeamDefinitionValidationError(
                "Team member count exceeds configured Runner ceiling"
            )
        if subagent_count > self.max_subagents:
            raise TeamDefinitionValidationError(
                "Team member count exceeds max_subagents"
            )
        if definition["max_steps"] > self.max_team_steps:
            raise TeamDefinitionValidationError(
                "Team max_steps exceeds configured Runner ceiling"
            )
        if definition["max_parallel_members"] > min(
            self.max_parallel_members, self.max_subagents, subagent_count
        ):
            raise TeamDefinitionValidationError(
                "Team max_parallel_members exceeds configured Runner ceiling"
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
