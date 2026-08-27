from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable
from typing import Any

import pytest

from archbro.integrations.firebase import auth as firebase_auth_module
from archbro.integrations.firebase.admin import FirebaseAdminUnavailable
from archbro.integrations.firebase.auth import (
    AuthenticationServiceUnavailable,
    InvalidAuthenticationToken,
    verify_firebase_id_token,
)


def _verify_with_claims(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
):
    monkeypatch.setattr(
        firebase_auth_module,
        "_verify_firebase_id_token_sync",
        lambda _token, _project_id: claims,
    )
    return asyncio.run(verify_firebase_id_token("test-token", "archbro-test"))


@pytest.mark.parametrize(
    ("provider", "expected_provider"),
    [
        ("password", "password"),
        ("google.com", "google.com"),
        ("github.com", "github.com"),
        (None, None),
    ],
)
def test_verified_firebase_uid_and_provider_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
    provider: str | None,
    expected_provider: str | None,
):
    firebase_claims = (
        {"sign_in_provider": provider}
        if provider is not None
        else {}
    )

    user = _verify_with_claims(
        monkeypatch,
        {
            "uid": "  firebase-user-123  ",
            "firebase": firebase_claims,
        },
    )

    assert user.uid == "firebase-user-123"
    assert user.sign_in_provider == expected_provider


def test_subject_is_used_when_uid_is_missing(monkeypatch: pytest.MonkeyPatch):
    user = _verify_with_claims(
        monkeypatch,
        {"sub": "firebase-user-from-sub"},
    )

    assert user.uid == "firebase-user-from-sub"
    assert user.sign_in_provider is None


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"uid": ""},
        {"uid": "   "},
        {"uid": None},
    ],
)
def test_missing_or_empty_user_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
):
    monkeypatch.setattr(
        firebase_auth_module,
        "_verify_firebase_id_token_sync",
        lambda _token, _project_id: claims,
    )

    with pytest.raises(
        InvalidAuthenticationToken,
        match="Firebase ID token has no user id",
    ):
        asyncio.run(verify_firebase_id_token("test-token", "archbro-test"))


@pytest.mark.parametrize(
    "claims",
    [
        {"uid": 123},
        {"uid": True},
        {"uid": []},
        {"uid": {}},
        {"sub": 123},
        {"sub": True},
        {"sub": []},
        {"sub": {}},
        {"uid": 123, "sub": "valid-sub-must-not-hide-malformed-uid"},
    ],
)
def test_non_string_user_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
):
    monkeypatch.setattr(
        firebase_auth_module,
        "_verify_firebase_id_token_sync",
        lambda _token, _project_id: claims,
    )

    with pytest.raises(
        InvalidAuthenticationToken,
        match="Firebase ID token has no user id",
    ):
        asyncio.run(verify_firebase_id_token("test-token", "archbro-test"))


def test_firebase_metadata_must_be_a_dictionary(
    monkeypatch: pytest.MonkeyPatch,
):
    user = _verify_with_claims(
        monkeypatch,
        {
            "uid": "firebase-user-123",
            "firebase": "unexpected metadata",
        },
    )

    assert user.uid == "firebase-user-123"
    assert user.sign_in_provider is None


@pytest.mark.parametrize("claims", [None, "claims", ["claims"]])
def test_non_dictionary_claims_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    claims: object,
):
    from firebase_admin import auth as firebase_auth

    monkeypatch.setattr(
        firebase_auth_module,
        "get_firebase_admin_app",
        lambda _project_id: object(),
    )
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        lambda _token, *, app: claims,
    )

    with pytest.raises(
        InvalidAuthenticationToken,
        match="Firebase ID token has invalid claims",
    ):
        firebase_auth_module._verify_firebase_id_token_sync(
            "test-token",
            "archbro-test",
        )


@pytest.mark.parametrize(
    "firebase_exception_name",
    [
        "InvalidIdTokenError",
        "ExpiredIdTokenError",
        "RevokedIdTokenError",
        "UserDisabledError",
        None,
    ],
)
def test_invalid_firebase_credentials_are_rejected_without_leaking_token(
    monkeypatch: pytest.MonkeyPatch,
    firebase_exception_name: str | None,
):
    from firebase_admin import auth as firebase_auth

    sensitive_token = "secret-token-that-must-not-appear"
    exception_type: type[Exception]
    if firebase_exception_name is None:
        exception_type = ValueError
    else:
        exception_type = type("SimulatedFirebaseCredentialError", (Exception,), {})
        monkeypatch.setattr(
            firebase_auth,
            firebase_exception_name,
            exception_type,
        )

    monkeypatch.setattr(
        firebase_auth_module,
        "get_firebase_admin_app",
        lambda _project_id: object(),
    )

    def raise_invalid_token(_token: str, *, app: object):
        raise exception_type("provider details containing " + sensitive_token)

    monkeypatch.setattr(firebase_auth, "verify_id_token", raise_invalid_token)

    with pytest.raises(InvalidAuthenticationToken) as raised:
        firebase_auth_module._verify_firebase_id_token_sync(
            sensitive_token,
            "archbro-test",
        )

    assert str(raised.value) == "Firebase ID token is invalid."
    rendered_traceback = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sensitive_token not in rendered_traceback


def test_firebase_admin_initialization_failure_is_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    def unavailable_admin(_project_id: str):
        raise FirebaseAdminUnavailable("simulated admin failure")

    monkeypatch.setattr(
        firebase_auth_module,
        "get_firebase_admin_app",
        unavailable_admin,
    )

    with pytest.raises(AuthenticationServiceUnavailable) as raised:
        firebase_auth_module._verify_firebase_id_token_sync(
            "test-token",
            "archbro-test",
        )

    assert str(raised.value) == "Firebase Admin could not be initialized."
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "simulated admin failure" not in "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )


def test_unexpected_firebase_failure_is_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    from firebase_admin import auth as firebase_auth

    sensitive_detail = "internal provider detail"
    monkeypatch.setattr(
        firebase_auth_module,
        "get_firebase_admin_app",
        lambda _project_id: object(),
    )

    def raise_provider_failure(_token: str, *, app: object):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(firebase_auth, "verify_id_token", raise_provider_failure)

    with pytest.raises(AuthenticationServiceUnavailable) as raised:
        firebase_auth_module._verify_firebase_id_token_sync(
            "test-token",
            "archbro-test",
        )

    assert str(raised.value) == "Firebase ID token verification is unavailable."
    rendered_traceback = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sensitive_detail not in rendered_traceback


def test_async_verification_dispatches_blocking_work_to_a_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def fake_to_thread(
        function: Callable[..., dict[str, Any]],
        *args: object,
    ) -> dict[str, Any]:
        captured["function"] = function
        captured["args"] = args
        return {
            "uid": "firebase-thread-user",
            "firebase": {"sign_in_provider": "password"},
        }

    monkeypatch.setattr(firebase_auth_module.asyncio, "to_thread", fake_to_thread)

    user = asyncio.run(
        verify_firebase_id_token("thread-test-token", "thread-test-project")
    )

    assert captured == {
        "function": firebase_auth_module._verify_firebase_id_token_sync,
        "args": ("thread-test-token", "thread-test-project"),
    }
    assert user.uid == "firebase-thread-user"
    assert user.sign_in_provider == "password"
