from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .models import Team, TeamVersion, TeamVersionImmutableError, TeamVersionMember


class TeamNotFoundError(KeyError):
    pass


class TeamDraftConflictError(ValueError):
    pass


class TeamDefinitionValidationError(ValueError):
    pass


class TeamRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        unit_id: str,
        project_id: str,
        name: str,
        created_by: str,
        description: str = "",
    ) -> Team:
        team = Team(
            unit_id=unit_id,
            project_id=project_id,
            name=name,
            description=description,
            created_by=created_by,
            updated_by=created_by,
        )
        self.session.add(team)
        self.session.flush()
        self.session.add(
            TeamVersion(
                team_id=team.id,
                version=0,
                status="draft",
                definition={},
                max_steps=1,
                max_parallel_members=1,
                timeout_seconds=1,
            )
        )
        self.session.flush()
        return team

    def get_scoped(self, unit_id: str, project_id: str, team_id: str) -> Team | None:
        return self.session.scalar(
            select(Team).where(
                Team.id == team_id,
                Team.unit_id == unit_id,
                Team.project_id == project_id,
            )
        )

    def list_scoped(self, unit_id: str, project_id: str) -> list[Team]:
        return list(
            self.session.scalars(
                select(Team)
                .where(Team.unit_id == unit_id, Team.project_id == project_id)
                .order_by(Team.name, Team.id)
            )
        )

    def get_version(self, team_id: str, version: int) -> TeamVersion | None:
        return self.session.scalar(
            select(TeamVersion)
            .options(selectinload(TeamVersion.members))
            .where(TeamVersion.team_id == team_id, TeamVersion.version == version)
        )

    def get_version_by_id(self, version_id: str) -> TeamVersion | None:
        return self.session.scalar(
            select(TeamVersion)
            .options(selectinload(TeamVersion.members))
            .where(TeamVersion.id == version_id)
        )

    def save_draft(
        self,
        team_id: str,
        *,
        expected_revision: int,
        definition: dict[str, Any],
        updated_by: str | None = None,
    ) -> TeamVersion:
        team = self._get_locked_team(team_id)
        self._assert_expected_revision(team, expected_revision)
        draft = self._get_locked_draft(team.id)
        normalized = self._normalize_definition(definition)
        self._replace_draft_definition(draft, normalized)
        team.draft_revision += 1
        if updated_by is not None:
            team.updated_by = updated_by
        self.session.flush()
        return draft

    def publish(
        self,
        team_id: str,
        *,
        expected_revision: int,
        definition_digest: str,
        published_by: str,
    ) -> TeamVersion:
        if len(definition_digest) != 64:
            raise TeamDefinitionValidationError("definition_digest must be a SHA-256 digest")
        team = self._get_locked_team(team_id)
        self._assert_expected_revision(team, expected_revision)
        draft = self._get_locked_draft(team.id)
        normalized = self._normalize_definition(draft.definition)
        next_version = int(
            self.session.scalar(
                select(func.coalesce(func.max(TeamVersion.version), 0)).where(
                    TeamVersion.team_id == team.id
                )
            )
        ) + 1
        published = TeamVersion(
            team_id=team.id,
            version=next_version,
            status="published",
            definition=deepcopy(normalized),
            tool_ids=deepcopy(normalized["tool_ids"]),
            skill_names=deepcopy(normalized["skill_names"]),
            knowledge_source_ids=deepcopy(normalized["knowledge_source_ids"]),
            max_steps=normalized["max_steps"],
            max_parallel_members=normalized["max_parallel_members"],
            timeout_seconds=normalized["timeout_seconds"],
            failure_strategy=normalized["failure_strategy"],
            approval_policy_id=normalized["approval_policy_id"],
            definition_digest=definition_digest,
            published_by=published_by,
            published_at=datetime.now(timezone.utc),
        )
        self.session.add(published)
        self.session.flush()
        for position, member in enumerate(self._members_from_definition(normalized)):
            self.session.add(self._member_record(published.id, member, position))
        team.published_version_id = published.id
        team.updated_by = published_by
        self.session.flush()
        return published

    def replace_published_definition(
        self, team_version_id: str, definition: dict[str, Any]
    ) -> None:
        version = self.get_version_by_id(team_version_id)
        if version is None:
            raise TeamNotFoundError(team_version_id)
        if version.status == "published":
            raise TeamVersionImmutableError("Published team versions are immutable")
        raise TeamDefinitionValidationError("Only draft versions can be replaced")

    def _get_locked_team(self, team_id: str) -> Team:
        team = self.session.scalar(select(Team).where(Team.id == team_id).with_for_update())
        if team is None:
            raise TeamNotFoundError(team_id)
        return team

    def _get_locked_draft(self, team_id: str) -> TeamVersion:
        draft = self.session.scalar(
            select(TeamVersion)
            .where(TeamVersion.team_id == team_id, TeamVersion.status == "draft")
            .with_for_update()
        )
        if draft is None:
            raise TeamDefinitionValidationError("Team has no draft definition")
        return draft

    @staticmethod
    def _assert_expected_revision(team: Team, expected_revision: int) -> None:
        if team.draft_revision != expected_revision:
            raise TeamDraftConflictError("Team draft revision changed concurrently")

    def _replace_draft_definition(
        self, draft: TeamVersion, definition: dict[str, Any]
    ) -> None:
        draft.definition = deepcopy(definition)
        draft.tool_ids = deepcopy(definition["tool_ids"])
        draft.skill_names = deepcopy(definition["skill_names"])
        draft.knowledge_source_ids = deepcopy(definition["knowledge_source_ids"])
        draft.max_steps = definition["max_steps"]
        draft.max_parallel_members = definition["max_parallel_members"]
        draft.timeout_seconds = definition["timeout_seconds"]
        draft.failure_strategy = definition["failure_strategy"]
        draft.approval_policy_id = definition["approval_policy_id"]
        self.session.execute(
            delete(TeamVersionMember).where(TeamVersionMember.team_version_id == draft.id)
        )
        self.session.flush()
        for position, member in enumerate(self._members_from_definition(definition)):
            self.session.add(self._member_record(draft.id, member, position))

    @staticmethod
    def _member_record(
        team_version_id: str, member: dict[str, Any], position: int
    ) -> TeamVersionMember:
        return TeamVersionMember(
            team_version_id=team_version_id,
            agent_id=member["agent_id"],
            agent_definition_digest=member.get("agent_definition_digest"),
            agent_definition=deepcopy(member.get("agent_definition")),
            role=member["role"],
            responsibility=member["responsibility"],
            position=position,
            tool_ids=deepcopy(member["tool_ids"]),
            skill_names=deepcopy(member["skill_names"]),
            knowledge_source_ids=deepcopy(member["knowledge_source_ids"]),
        )

    @staticmethod
    def _members_from_definition(definition: dict[str, Any]) -> list[dict[str, Any]]:
        return [definition["supervisor"], *definition["members"]]

    @classmethod
    def _normalize_definition(cls, definition: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(definition, dict):
            raise TeamDefinitionValidationError("Team definition must be an object")
        supervisor = cls._normalize_member(definition.get("supervisor"), "supervisor")
        raw_members = definition.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            raise TeamDefinitionValidationError("Team requires at least one member")
        members = [cls._normalize_member(member, "member") for member in raw_members]
        agent_ids = [member["agent_id"] for member in [supervisor, *members]]
        if len(agent_ids) != len(set(agent_ids)):
            raise TeamDefinitionValidationError("Team supervisor and members must be distinct")
        return {
            "supervisor": supervisor,
            "members": members,
            "tool_ids": cls._string_list(definition.get("tool_ids", []), "tool_ids"),
            "skill_names": cls._string_list(definition.get("skill_names", []), "skill_names"),
            "knowledge_source_ids": cls._string_list(
                definition.get("knowledge_source_ids", []), "knowledge_source_ids"
            ),
            "max_steps": cls._positive_int(definition.get("max_steps"), "max_steps"),
            "max_parallel_members": cls._positive_int(
                definition.get("max_parallel_members"), "max_parallel_members"
            ),
            "timeout_seconds": cls._positive_int(
                definition.get("timeout_seconds"), "timeout_seconds"
            ),
            "failure_strategy": cls._non_empty_string(
                definition.get("failure_strategy", "fail_fast"), "failure_strategy"
            ),
            "approval_policy_id": cls._optional_string(
                definition.get("approval_policy_id"), "approval_policy_id"
            ),
        }

    @classmethod
    def _normalize_member(cls, member: Any, role: str) -> dict[str, Any]:
        if not isinstance(member, dict) or member.get("actor_type") == "team":
            raise TeamDefinitionValidationError("Nested teams are not valid members")
        digest = member.get("agent_definition_digest", member.get("agent_version_id"))
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
            raise TeamDefinitionValidationError("agent_definition_digest must be a SHA-256 digest")
        agent_definition = member.get("agent_definition")
        if agent_definition is not None and not isinstance(agent_definition, dict):
            raise TeamDefinitionValidationError("agent_definition must be an object")
        return {
            "agent_id": cls._non_empty_string(member.get("agent_id"), "agent_id"),
            "agent_definition_digest": digest,
            "agent_definition": deepcopy(agent_definition),
            "role": role,
            "responsibility": cls._non_empty_string(
                member.get("responsibility"), "responsibility"
            ),
            "tool_ids": cls._string_list(member.get("tool_ids", []), "tool_ids"),
            "skill_names": cls._string_list(member.get("skill_names", []), "skill_names"),
            "knowledge_source_ids": cls._string_list(
                member.get("knowledge_source_ids", []), "knowledge_source_ids"
            ),
        }

    @staticmethod
    def _non_empty_string(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TeamDefinitionValidationError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _optional_string(value: Any, field: str) -> str | None:
        if value is None:
            return None
        return TeamRepository._non_empty_string(value, field)

    @staticmethod
    def _string_list(value: Any, field: str) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise TeamDefinitionValidationError(f"{field} must be a list of strings")
        return list(value)

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise TeamDefinitionValidationError(f"{field} must be positive")
        return value
