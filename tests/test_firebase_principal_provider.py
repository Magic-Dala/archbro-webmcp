from __future__ import annotations

import asyncio
import inspect
import traceback

import pytest

from archbro.backend.core.authorization import (
    IdentityProviderUnavailableError,
    InvalidCredentialsError,
    TrustedPrincipal,
)
from archbro.integrations.firebase.auth import (
    AuthenticatedUser,
    AuthenticationServiceUnavailable,
    InvalidAuthenticationToken,
)
from archbro.integrations.firebase import FirebasePrincipalProvider


@pytest.mark.parametrize(
    "sign_in_provider",
    ["password", "google.com", "github.com"],
)
def test_verified_firebase_uid_becomes_canonical_trusted_user_id(
    sign_in_provider: str,
):
    received: list[tuple[str, str]] = []

    async def verifier(token: str, project_id: str) -> AuthenticatedUser:
        received.append((token, project_id))
        return AuthenticatedUser(
            uid="firebase-uid-alice",
            sign_in_provider=sign_in_provider,
        )

    provider = FirebasePrincipalProvider(
        project_id="archbro-test",
        verifier=verifier,
    )

    principal = asyncio.run(provider("signed-firebase-token"))

    assert principal == TrustedPrincipal(
        user_id="firebase-uid-alice",
        team_ids=[],
        local_development=False,
    )
    assert received == [("signed-firebase-token", "archbro-test")]


def test_provider_awaits_the_configured_async_verifier():
    verifier_finished = False

    async def verifier(_token: str, _project_id: str) -> AuthenticatedUser:
        nonlocal verifier_finished
        await asyncio.sleep(0)
        verifier_finished = True
        return AuthenticatedUser(uid="firebase-uid-alice")

    provider = FirebasePrincipalProvider("archbro-test", verifier=verifier)

    principal = asyncio.run(provider("signed-firebase-token"))

    assert verifier_finished is True
    assert principal.user_id == "firebase-uid-alice"


def test_provider_call_boundary_accepts_only_the_token():
    provider = FirebasePrincipalProvider("archbro-test")

    assert list(inspect.signature(provider).parameters) == ["token"]


def test_invalid_firebase_token_maps_to_backend_invalid_credentials_error():
    sensitive_token = "secret-token-that-must-not-appear"

    async def verifier(token: str, _project_id: str) -> AuthenticatedUser:
        raise InvalidAuthenticationToken("invalid provider detail: " + token)

    provider = FirebasePrincipalProvider("archbro-test", verifier=verifier)

    with pytest.raises(InvalidCredentialsError) as raised:
        asyncio.run(provider(sensitive_token))

    assert str(raised.value) == "Firebase ID token is invalid."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sensitive_token not in str(raised.value)
    formatted_error = "".join(traceback.format_exception(raised.value))
    assert sensitive_token not in formatted_error


def test_anonymous_firebase_identity_is_not_trusted():
    async def verifier(_token: str, _project_id: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            uid="anonymous-firebase-uid",
            sign_in_provider="anonymous",
        )

    provider = FirebasePrincipalProvider("archbro-test", verifier=verifier)

    with pytest.raises(InvalidCredentialsError) as raised:
        asyncio.run(provider("anonymous-firebase-token"))

    assert str(raised.value) == "Firebase ID token is invalid."


def test_firebase_outage_maps_to_backend_identity_provider_unavailable_error():
    sensitive_detail = "internal Firebase outage detail"

    async def verifier(_token: str, _project_id: str) -> AuthenticatedUser:
        raise AuthenticationServiceUnavailable(sensitive_detail)

    provider = FirebasePrincipalProvider("archbro-test", verifier=verifier)

    with pytest.raises(IdentityProviderUnavailableError) as raised:
        asyncio.run(provider("signed-firebase-token"))

    assert str(raised.value) == "Firebase identity provider is unavailable."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sensitive_detail not in str(raised.value)
    formatted_error = "".join(traceback.format_exception(raised.value))
    assert sensitive_detail not in formatted_error


def test_multiple_calls_do_not_leak_identity_between_requests():
    requested_users = iter(["firebase-uid-alice", "firebase-uid-bob"])

    async def verifier(_token: str, _project_id: str) -> AuthenticatedUser:
        return AuthenticatedUser(uid=next(requested_users))

    provider = FirebasePrincipalProvider("archbro-test", verifier=verifier)

    async def authenticate_two_users() -> tuple[TrustedPrincipal, TrustedPrincipal]:
        alice = await provider("alice-token")
        bob = await provider("bob-token")
        return alice, bob

    alice, bob = asyncio.run(authenticate_two_users())

    assert alice.user_id == "firebase-uid-alice"
    assert bob.user_id == "firebase-uid-bob"
    assert alice.team_ids == []
    assert bob.team_ids == []
    assert alice is not bob
