from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from archbro.backend.core.authorization import (
    IdentityProviderUnavailableError,
    InvalidCredentialsError,
    TrustedPrincipal,
)
from archbro.integrations.firebase.auth import (
    AuthenticatedUser,
    AuthenticationServiceUnavailable,
    InvalidAuthenticationToken,
    verify_firebase_id_token,
)


FirebaseTokenVerifier = Callable[[str, str], Awaitable[AuthenticatedUser]]


@dataclass(frozen=True, slots=True)
class FirebasePrincipalProvider:
    """Convert a verified Firebase user into Archbro's trusted identity."""

    project_id: str
    verifier: FirebaseTokenVerifier = verify_firebase_id_token

    async def __call__(self, token: str) -> TrustedPrincipal:
        authentication_error: (
            InvalidCredentialsError | IdentityProviderUnavailableError | None
        ) = None
        try:
            authenticated_user = await self.verifier(token, self.project_id)
        except InvalidAuthenticationToken:
            authentication_error = InvalidCredentialsError(
                "Firebase ID token is invalid."
            )
        except AuthenticationServiceUnavailable:
            authentication_error = IdentityProviderUnavailableError(
                "Firebase identity provider is unavailable."
            )

        # Raise after leaving the provider exception handler so Python does not
        # retain Firebase's original exception as cause or context.
        if authentication_error is not None:
            raise authentication_error

        if authenticated_user.sign_in_provider == "anonymous":
            raise InvalidCredentialsError("Firebase ID token is invalid.")

        return TrustedPrincipal(
            user_id=authenticated_user.uid,
            team_ids=[],
            local_development=False,
        )
