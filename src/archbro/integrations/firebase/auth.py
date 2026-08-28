from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from archbro.backend.core.authorization import (
    IdentityProviderUnavailableError,
    InvalidCredentialsError,
    PrincipalProvider,
    TrustedPrincipal,
)
from archbro.integrations.firebase.admin import (
    FirebaseAdminUnavailable,
    get_firebase_admin_app,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Normalized identity Archbro is allowed to trust."""

    uid: str
    sign_in_provider: str | None = None


class InvalidAuthenticationToken(ValueError):
    """The Firebase token is missing, expired, revoked, or invalid."""


class AuthenticationServiceUnavailable(RuntimeError):
    """Firebase verification could not run because the service is unavailable."""


def _verify_firebase_id_token_sync(token: str, project_id: str) -> dict[str, Any]:
    try:
        from firebase_admin import auth as firebase_auth
    except Exception:
        firebase_auth = None

    if firebase_auth is None:
        raise AuthenticationServiceUnavailable(
            "Firebase Admin is not installed."
        )

    verification_failure: tuple[type[Exception], str] | None = None
    claims: Any = None
    try:
        claims = firebase_auth.verify_id_token(
            token,
            app=get_firebase_admin_app(project_id),
        )
    except FirebaseAdminUnavailable:
        verification_failure = (
            AuthenticationServiceUnavailable,
            "Firebase Admin could not be initialized.",
        )
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.ExpiredIdTokenError,
        firebase_auth.RevokedIdTokenError,
        firebase_auth.UserDisabledError,
        ValueError,
    ):
        verification_failure = (
            InvalidAuthenticationToken,
            "Firebase ID token is invalid.",
        )
    except Exception:
        verification_failure = (
            AuthenticationServiceUnavailable,
            "Firebase ID token verification is unavailable.",
        )

    # Raise after leaving the provider exception handler so Python does not attach
    # Firebase's original exception (which may contain sensitive details) as the
    # cause or context of the public Archbro error.
    if verification_failure is not None:
        error_type, message = verification_failure
        raise error_type(message)

    if not isinstance(claims, dict):
        raise InvalidAuthenticationToken("Firebase ID token has invalid claims.")
    return claims


async def verify_firebase_id_token(token: str, project_id: str) -> AuthenticatedUser:
    """Verify a Firebase ID token without blocking the FastAPI event loop."""

    claims = await asyncio.to_thread(
        _verify_firebase_id_token_sync, token, project_id
    )
    uid_value = claims.get("uid")
    if uid_value is None:
        uid_value = claims.get("sub")
    if not isinstance(uid_value, str) or not uid_value.strip():
        raise InvalidAuthenticationToken("Firebase ID token has no user id.")
    uid = uid_value.strip()

    firebase_claims = claims.get("firebase")
    provider = (
        firebase_claims.get("sign_in_provider")
        if isinstance(firebase_claims, dict)
        else None
    )
    return AuthenticatedUser(
        uid=uid,
        sign_in_provider=str(provider) if provider is not None else None,
    )


def firebase_principal_provider(project_id: str) -> PrincipalProvider:
    """Bind verified Firebase identity to Archbro's trusted-principal contract."""

    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise ValueError("Firebase project id is required for trusted authentication.")

    async def provide(token: str) -> TrustedPrincipal:
        try:
            user = await verify_firebase_id_token(token, normalized_project_id)
        except InvalidAuthenticationToken:
            raise InvalidCredentialsError("firebase token is invalid") from None
        except AuthenticationServiceUnavailable:
            raise IdentityProviderUnavailableError("firebase authentication is unavailable") from None
        return TrustedPrincipal(user_id=user.uid, team_ids=[])

    return provide
