from pydantic import ValidationError
import pytest

from archbro.backend.api.routes import EventRequest
from archbro.backend.core.contracts import (
    GitHubChangePayload,
    ProjectEventSource,
    ProjectEventType,
)


def test_push_payload_is_normalized_to_canonical_github_contract():
    payload = GitHubChangePayload.model_validate(
        {
            "repository": " Magic-Dala/archbro ",
            "event_kind": "PUSH",
            "summary": " Backend API changed. ",
            "ref": " refs/heads/main ",
            "commit_sha": " abc123 ",
            "actor": " developer ",
            "changed_files": [" backend/api.py ", "backend/api.py"],
            "commits": ["abc123", " abc123 "],
        }
    )

    assert payload.model_dump(mode="json", exclude_none=True) == {
        "repository": "Magic-Dala/archbro",
        "event_kind": "PUSH",
        "summary": "Backend API changed.",
        "ref": "refs/heads/main",
        "commit_sha": "abc123",
        "actor": "developer",
        "changed_files": ["backend/api.py"],
        "commits": ["abc123"],
    }


def test_merged_pull_request_requires_pr_number_and_merge_commit():
    with pytest.raises(ValidationError, match="pull_request_number"):
        GitHubChangePayload.model_validate(
            {
                "repository": "Magic-Dala/archbro",
                "event_kind": "PULL_REQUEST_MERGED",
                "summary": "Merged backend change.",
                "commit_sha": "merge123",
            }
        )

    valid = GitHubChangePayload.model_validate(
        {
            "repository": "Magic-Dala/archbro",
            "event_kind": "PULL_REQUEST_MERGED",
            "summary": "Merged backend change.",
            "commit_sha": "merge456",
            "pull_request_number": 42,
            "title": "Add backend API",
        }
    )
    assert valid.pull_request_number == 42
    assert valid.commit_sha == "merge456"


def test_public_event_request_cannot_self_assert_trusted_provider_source():
    with pytest.raises(ValidationError, match="verified server-side integration"):
        EventRequest(
            type=ProjectEventType.USER_MESSAGE,
            source=ProjectEventSource.GITHUB,
            source_event_id="delivery-1",
            payload={"message": "caller claims to be GitHub"},
        )

    with pytest.raises(ValidationError, match="verified GitHub integration boundary"):
        EventRequest(
            type=ProjectEventType.GITHUB_CHANGE,
            source=ProjectEventSource.FRONTEND,
            source_event_id="delivery-2",
            payload={
                "repository": "Magic-Dala/archbro",
                "event_kind": "PUSH",
                "summary": "Backend changed.",
                "ref": "refs/heads/main",
                "commit_sha": "abc123",
            },
        )
