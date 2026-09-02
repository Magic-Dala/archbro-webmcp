"""The GitHub side of the pull-based signal pipeline.

The adapter is the only provider-specific piece: it names the MCP tool, turns a
stored cursor position into tool arguments, and turns a raw result into
normalized signals. Everything downstream of it is provider-agnostic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from archbro.backend.core.contracts import (
    GitHubChangePayload,
    ProjectEventSource,
    ProjectEventType,
)
from archbro.integrations.github.adapter import GitHubCommitAdapter


REPOSITORY_ID = 987654321

#: A connector that already took its baseline and is now watching.
WATCHING = "2026-09-01T00:00:00Z"


def _adapter(**overrides) -> GitHubCommitAdapter:
    settings = {
        "repository_id": REPOSITORY_ID,
        "repository": "Magic-Dala/archbro",
        "branch": "main",
    }
    settings.update(overrides)
    return GitHubCommitAdapter(**settings)


def _commit(sha: str, *, date: str, message: str = "Change the payment module") -> dict:
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "author": {"name": "A Developer", "date": date},
        },
        "author": {"login": "a-developer"},
    }


def _structured(commits: list[dict]) -> dict:
    return {"structuredContent": {"commits": commits}}


def _as_text_content(commits: list[dict]) -> dict:
    """The shape an MCP server returns when it has no structured output."""
    return {"content": [{"type": "text", "text": json.dumps(commits)}]}


def test_adapter_reads_the_commit_listing_tool():
    assert _adapter().tool_name == "list_commits"


def test_first_pass_asks_for_the_branch_without_a_since_bound():
    arguments = _adapter().build_arguments(None)

    assert arguments["owner"] == "Magic-Dala"
    assert arguments["repo"] == "archbro"
    assert arguments["sha"] == "main"
    assert "since" not in arguments


def test_later_passes_bound_the_read_by_the_stored_position():
    arguments = _adapter().build_arguments("2026-09-01T10:00:00Z")

    assert arguments["since"] == "2026-09-01T10:00:00Z"


def test_each_commit_becomes_one_github_change_signal():
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    result = _adapter().normalize(raw, WATCHING)

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.source == ProjectEventSource.GITHUB
    assert signal.event_type == ProjectEventType.GITHUB_CHANGE
    assert signal.occurred_at is not None
    assert signal.occurred_at.isoformat().startswith("2026-09-01T12:00:00")


def test_the_replay_key_identifies_the_repository_by_its_permanent_id():
    # The tag is compared as a string, so it must not carry anything that can be
    # spelled two ways for one repository. GitHub treats owner/name as
    # case-insensitive and lets it change on rename or transfer; the numeric id
    # does neither.
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    signal = _adapter().normalize(raw, WATCHING).signals[0]

    assert signal.source_event_id == f"github:{REPOSITORY_ID}:main:commit:abc123"


def test_two_spellings_of_one_repository_agree_on_identity_and_payload():
    # Matching identities whose payloads differ are rejected permanently, so
    # pinning only the key would have documented a configuration that breaks
    # replay rather than one that survives it.
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    upper = _adapter(repository="Magic-Dala/archbro").normalize(raw, WATCHING).signals[0]
    lower = _adapter(repository="magic-dala/archbro").normalize(raw, WATCHING).signals[0]

    assert upper.source_event_id == lower.source_event_id
    assert upper.payload == lower.payload


def test_the_payload_keeps_the_repository_name_people_read():
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    signal = _adapter().normalize(raw, WATCHING).signals[0]

    # Lowercased: the payload is compared byte for byte on replay, so the
    # spelling in configuration must not reach it.
    assert signal.payload["repository"] == "magic-dala/archbro"


def test_the_payload_satisfies_the_canonical_github_change_contract():
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    signal = _adapter().normalize(raw, WATCHING).signals[0]

    payload = GitHubChangePayload.model_validate(signal.payload)
    assert payload.event_kind == "PUSH"
    assert payload.ref == "refs/heads/main"
    assert payload.commit_sha == "abc123"
    assert payload.actor == "a-developer"


def test_the_summary_is_the_first_line_of_the_commit_message():
    raw = _structured(
        [
            _commit(
                "abc123",
                date="2026-09-01T12:00:00Z",
                message="Split the payment module\n\nLonger body that is not a summary.",
            )
        ]
    )

    signal = _adapter().normalize(raw, WATCHING).signals[0]

    assert signal.payload["summary"] == "Split the payment module"


def test_results_delivered_as_json_text_are_read_the_same_way():
    # Not every MCP server fills structuredContent; the text block carrying JSON
    # is the common fallback and must not be silently dropped.
    raw = _as_text_content([_commit("abc123", date="2026-09-01T12:00:00Z")])

    result = _adapter().normalize(raw, WATCHING)

    assert [signal.source_event_id for signal in result.signals] == [
        f"github:{REPOSITORY_ID}:main:commit:abc123"
    ]


def test_the_position_advances_to_the_newest_commit_seen():
    raw = _structured(
        [
            _commit("newest", date="2026-09-01T12:00:00Z"),
            _commit("oldest", date="2026-09-01T09:00:00Z"),
        ]
    )

    result = _adapter().normalize(raw, WATCHING)

    assert result.next_position == "2026-09-01T12:00:00Z"


def test_an_empty_read_leaves_the_position_where_it_was():
    result = _adapter().normalize(_structured([]), "2026-09-01T10:00:00Z")

    assert result.signals == []
    assert result.next_position is None


def test_a_commit_without_a_sha_is_refused_rather_than_given_a_broken_key():
    # A signal whose identity is "github:<id>:commit:" would collide with every
    # other malformed commit and permanently poison replay protection.
    raw = _structured([{"commit": {"message": "no sha", "author": {"date": "2026-09-01T12:00:00Z"}}}])

    with pytest.raises(ValueError, match="sha"):
        _adapter().normalize(raw, WATCHING)


def test_the_repository_must_be_owner_and_name():
    with pytest.raises(ValueError, match="owner/name"):
        _adapter(repository="archbro")


# --- Failures found in review of the first implementation ---------------------


def test_the_same_commit_on_two_branches_gets_two_identities():
    # The backend compares the whole payload on replay and rejects a matching
    # identity whose data differs, permanently. Reading one commit from two
    # branches yields two payloads (their ref differs), so the identities must
    # differ too or the second branch is silently and permanently refused.
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    main = _adapter(branch="main").normalize(raw, WATCHING).signals[0]
    release = _adapter(branch="release").normalize(raw, WATCHING).signals[0]

    assert main.payload["ref"] != release.payload["ref"]
    assert main.source_event_id != release.source_event_id


def test_reading_the_same_result_twice_produces_the_same_payload():
    raw = _structured([_commit("abc123", date="2026-09-01T12:00:00Z")])

    first = _adapter().normalize(raw, WATCHING).signals[0]
    second = _adapter().normalize(raw, WATCHING).signals[0]

    assert first.source_event_id == second.source_event_id
    assert first.payload == second.payload


def test_a_tool_error_is_raised_rather_than_read_as_no_commits():
    # An empty read is indistinguishable from "nothing changed": the cursor
    # holds, the pass reports success, and an expired token or rate limit looks
    # exactly like a quiet repository.
    raw = {"isError": True, "content": [{"type": "text", "text": "rate limited"}]}

    with pytest.raises(ValueError, match="rate limited"):
        _adapter().normalize(raw, WATCHING)


def test_an_unrecognised_result_shape_is_raised_rather_than_read_as_no_commits():
    with pytest.raises(ValueError, match="could not be read"):
        _adapter().normalize({"content": [{"type": "text", "text": "not json"}]}, WATCHING)


def test_an_explicitly_empty_listing_is_not_an_error():
    result = _adapter().normalize(_structured([]), WATCHING)

    assert result.signals == []


def test_a_position_written_before_windowing_is_still_read_as_a_lower_bound():
    assert _adapter().build_arguments("2026-09-01T10:00:00Z")["since"] == (
        "2026-09-01T10:00:00Z"
    )


# --- Reading a window without assuming how GitHub orders it -------------------

FROZEN_NOW = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)


def _clocked(**overrides) -> GitHubCommitAdapter:
    return _adapter(now=lambda: FROZEN_NOW, **overrides)


def test_connecting_a_repository_starts_watching_from_now():
    # Replaying history evaluates commits against an architecture that did not
    # exist when they were made. Taking the clock rather than the newest commit
    # also avoids assuming which end of the list GitHub returns first.
    result = _clocked().normalize(_structured([_commit("old", date="2020-01-01T00:00:00Z")]), None)

    assert result.signals == []
    assert result.next_position == FROZEN_NOW.isoformat()


def test_a_full_page_freezes_the_window_and_pages_through_it():
    # A full page means the result set was truncated, but not which end was cut.
    # Freezing both bounds makes the set immutable, so paging enumerates all of
    # it regardless of order, and commits arriving mid-walk fall to the next pass.
    adapter = _clocked(page_size=2)
    raw = _structured(
        [
            _commit("a", date="2026-09-01T12:00:00Z"),
            _commit("b", date="2026-09-01T11:00:00Z"),
        ]
    )

    result = adapter.normalize(raw, "2026-09-01T08:00:00Z")

    arguments = adapter.build_arguments(result.next_position)
    assert arguments["since"] == "2026-09-01T08:00:00Z"
    assert arguments["until"] == FROZEN_NOW.isoformat()
    assert arguments["page"] == 1


def test_paging_walks_forward_until_the_window_is_drained():
    adapter = _clocked(page_size=2)
    full = _structured(
        [
            _commit("a", date="2026-09-01T12:00:00Z"),
            _commit("b", date="2026-09-01T11:00:00Z"),
        ]
    )

    first = adapter.normalize(full, "2026-09-01T08:00:00Z")
    second = adapter.normalize(full, first.next_position)

    assert adapter.build_arguments(second.next_position)["page"] == 2


def test_draining_the_window_checkpoints_at_the_frozen_upper_bound():
    adapter = _clocked(page_size=2)
    first = adapter.normalize(
        _structured(
            [
                _commit("a", date="2026-09-01T12:00:00Z"),
                _commit("b", date="2026-09-01T11:00:00Z"),
            ]
        ),
        "2026-09-01T08:00:00Z",
    )
    drained = adapter.normalize(
        _structured([_commit("c", date="2026-09-01T09:00:00Z")]),
        first.next_position,
    )

    arguments = adapter.build_arguments(drained.next_position)
    assert arguments["since"] == FROZEN_NOW.isoformat()
    assert "until" not in arguments
    assert "page" not in arguments


def test_a_full_page_of_commits_sharing_one_timestamp_still_makes_progress():
    # The previous strategy narrowed by timestamp and could not get past a tie.
    # Paging does not care that they are equal.
    adapter = _clocked(page_size=2)
    tied = _structured(
        [
            _commit("a", date="2026-09-01T12:00:00Z"),
            _commit("b", date="2026-09-01T12:00:00Z"),
        ]
    )

    first = adapter.normalize(tied, "2026-09-01T08:00:00Z")
    second = adapter.normalize(tied, first.next_position)

    assert adapter.build_arguments(second.next_position)["page"] == 2


def test_a_short_first_page_needs_no_second_read():
    adapter = _clocked(page_size=10)

    result = adapter.normalize(
        _structured([_commit("a", date="2026-09-01T12:00:00Z")]),
        "2026-09-01T08:00:00Z",
    )

    arguments = adapter.build_arguments(result.next_position)
    assert "until" not in arguments
    assert arguments["since"] == "2026-09-01T12:00:00Z"


def test_an_empty_repository_still_records_where_watching_started():
    # Leaving the position unset would make the next pass another baseline, so
    # the connector would never deliver anything once commits did appear.
    result = _clocked().normalize(_structured([]), None)

    assert result.next_position == FROZEN_NOW.isoformat()


class _TickingClock:
    """Hands out increasing instants and records when each was taken."""

    def __init__(self, *instants: datetime) -> None:
        self._instants = list(instants)
        self.reads = 0

    def __call__(self) -> datetime:
        instant = self._instants[min(self.reads, len(self._instants) - 1)]
        self.reads += 1
        return instant


def test_the_baseline_boundary_is_taken_before_the_read_not_after():
    """A commit created while the read is in flight must not fall in a gap.

    Sampling the clock after the response arrives leaves one: a commit created
    after GitHub built the response but before the sample is absent from what
    was read *and* older than the stored bound, so the next pass skips it for
    good. Taking the boundary first makes the worst case a re-read, which
    deduplicates, instead of a loss.
    """
    clock = _TickingClock(
        datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 6, 5, tzinfo=timezone.utc),
    )
    adapter = _adapter(now=clock)

    adapter.build_arguments(None)

    assert clock.reads >= 1, "the boundary must be sampled before the request is built"
    result = adapter.normalize(_structured([]), None)
    assert result.next_position == "2026-09-02T06:00:00+00:00"


def test_a_commit_arriving_during_the_baseline_read_is_delivered_next_pass():
    clock = _TickingClock(
        datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 6, 5, tzinfo=timezone.utc),
    )
    adapter = _adapter(now=clock)
    adapter.build_arguments(None)
    baseline = adapter.normalize(_structured([]), None)

    # Authored while the baseline read was in flight.
    in_flight = _commit("raced", date="2026-09-02T06:02:00Z")
    following = adapter.normalize(_structured([in_flight]), baseline.next_position)

    assert [s.payload["commit_sha"] for s in following.signals] == ["raced"]
