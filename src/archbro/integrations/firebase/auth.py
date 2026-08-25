from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

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
    except Exception as exc:
        raise AuthenticationServiceUnavailable(
            "Firebase Admin is not installed."
        ) from exc

    try:
        claims = firebase_auth.verify_id_token(
            token,
            app=get_firebase_admin_app(project_id),
        )
    except FirebaseAdminUnavailable as exc:
        raise AuthenticationServiceUnavailable(
            "Firebase Admin could not be initialized."
        ) from exc
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.ExpiredIdTokenError,
        firebase_auth.RevokedIdTokenError,
        firebase_auth.UserDisabledError,
        ValueError,
    ) as exc:
        raise InvalidAuthenticationToken("Firebase ID token is invalid.") from exc
    except Exception as exc:
        raise AuthenticationServiceUnavailable(
            "Firebase ID token verification is unavailable."
        ) from exc

    if not isinstance(claims, dict):
        raise InvalidAuthenticationToken("Firebase ID token has invalid claims.")
    return claims


async def verify_firebase_id_token(token: str, project_id: str) -> AuthenticatedUser:
    """Verify a Firebase ID token without blocking the FastAPI event loop."""

    claims = await asyncio.to_thread(
        _verify_firebase_id_token_sync, token, project_id
    )
    uid_value = claims.get("uid") or claims.get("sub")
    uid = str(uid_value).strip() if uid_value is not None else ""
    if not uid:
        raise InvalidAuthenticationToken("Firebase ID token has no user id.")

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
