from dataclasses import replace

from .schemas import (
    AuthorizationContext,
    PermissionCapability,
    PermissionGrant,
    ResourceScope,
)


class AuthorizationService:
    def load_context(self, session, session_id: str) -> AuthorizationContext:
        from .repository import AuthorizationRepository

        return AuthorizationRepository(session).load_context(session_id)

    @staticmethod
    def _grant_allows(grant: PermissionGrant, resource: ResourceScope, user_id: str) -> bool:
        if grant.data_scope == "unit":
            return True
        if resource.project_id is None or resource.project_id not in grant.project_ids:
            return False
        if grant.data_scope in {"assigned_projects", "project", "custom_projects"}:
            return True
        return grant.data_scope == "own" and (
            (grant.owner_user_id or user_id) == (resource.owner_user_id or "")
        )

    def allows(
        self,
        context: AuthorizationContext,
        permission_code: str,
        resource: ResourceScope,
    ) -> bool:
        if resource.unit_id != context.unit_id:
            return False
        return any(
            grant.permission_code == permission_code
            and self._grant_allows(grant, resource, context.user_id)
            for grant in context.grants
        )

    def allows_entry(
        self,
        context: AuthorizationContext,
        permission_code: str,
        target: str,
    ) -> bool:
        if target == "unit":
            return any(
                grant.permission_code == permission_code and grant.data_scope == "unit"
                for grant in context.grants
            )
        if target != "current_project" or context.current_project_id is None:
            return False
        return self.allows(
            context,
            permission_code,
            ResourceScope(context.unit_id, context.current_project_id, context.user_id),
        )

    def entry_capabilities(self, context: AuthorizationContext) -> tuple[PermissionCapability, ...]:
        capabilities: set[PermissionCapability] = set()
        for grant in context.grants:
            if self.allows_entry(context, grant.permission_code, "unit"):
                capabilities.add(PermissionCapability(grant.permission_code, "unit"))
            if self.allows_entry(context, grant.permission_code, "current_project"):
                capabilities.add(PermissionCapability(grant.permission_code, "current_project"))
        return tuple(sorted(capabilities))

    @staticmethod
    def with_project(context: AuthorizationContext, project_id: str | None) -> AuthorizationContext:
        return context.model_copy(update={"current_project_id": project_id})
