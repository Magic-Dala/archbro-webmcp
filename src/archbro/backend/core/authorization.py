from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from archbro.backend.core.contracts import Project


class ProjectPermission(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    REVIEW = "REVIEW"
    MANAGE = "MANAGE"


class TrustedPrincipal(BaseModel):
    """Server-trusted identity produced by the authentication integration.

    For the Firebase MVP, ``user_id`` is the verified Firebase UID. The backend
    never derives trusted identity from arbitrary request headers or user-supplied
    fields. ``team_ids`` may remain empty for the first owner-only MVP.
    """

    user_id: str
    team_ids: list[str] = Field(default_factory=list)
    local_development: bool = False

    @field_validator("user_id")
    @classmethod
    def require_user_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("trusted principal user_id must not be empty")
        return value

    @field_validator("team_ids")
    @classmethod
    def normalize_team_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized


class AuthenticationError(Exception):
    """Base error exposed by a trusted identity provider."""


class InvalidCredentialsError(AuthenticationError):
    """Bearer credentials are invalid, expired, or otherwise unverifiable."""


class IdentityProviderUnavailableError(AuthenticationError):
    """The trusted identity provider is temporarily unavailable."""


class PrincipalProvider(Protocol):
    async def __call__(self, token: str) -> TrustedPrincipal: ...


class ProjectAuthorizationError(PermissionError):
    pass


class ProjectAuthorizer:
    """Jim-owned project authorization policy.

    Authentication establishes the trusted principal. This policy decides what that
    principal may do inside one project. Owners can manage membership/lifecycle;
    explicit project members and trusted team members may read, write, and review.
    """

    _MEMBER_PERMISSIONS = {
        ProjectPermission.READ,
        ProjectPermission.WRITE,
        ProjectPermission.REVIEW,
    }

    def require(
        self,
        principal: TrustedPrincipal,
        project: Project,
        permission: ProjectPermission,
    ) -> None:
        if project.owner_user_id is None:
            if principal.local_development:
                return
            raise ProjectAuthorizationError("project has no trusted owner")

        if principal.user_id == project.owner_user_id:
            return

        is_project_member = principal.user_id in project.member_user_ids
        is_team_member = bool(project.team_id and project.team_id in principal.team_ids)
        if permission in self._MEMBER_PERMISSIONS and (is_project_member or is_team_member):
            return

        raise ProjectAuthorizationError(f"{permission.value.lower()} access denied for project")

    def can_read(self, principal: TrustedPrincipal, project: Project) -> bool:
        try:
            self.require(principal, project, ProjectPermission.READ)
        except ProjectAuthorizationError:
            return False
        return True


def local_development_principal() -> TrustedPrincipal:
    """Deterministic local-demo identity used when no auth provider is configured."""

    return TrustedPrincipal(user_id="local-demo", local_development=True)
