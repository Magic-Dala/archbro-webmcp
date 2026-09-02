"""Turn GitHub commit listings into normalized signals.

This is the only GitHub-specific piece of the pull-based pipeline. It reads
through the connected MCP gateway rather than talking to the GitHub API
directly, so credentials and transport stay deployment configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from archbro.backend.core.contracts import (
    GitHubChangeKind,
    GitHubChangePayload,
    ProjectEventSource,
    ProjectEventType,
)
from archbro.platform.pipeline.contracts import AdapterResult, NormalizedSignal


@dataclass(frozen=True, slots=True)
class _Window:
    """Where this connector is inside the commit history.

    ``since`` alone is the drained checkpoint. While a window is being read,
    ``until`` and ``page`` appear as well: a full page means the result set was
    truncated, but not which end of it was cut, and ``list_commits`` offers no
    way to ask. Freezing both bounds makes the set immutable, so paging
    enumerates all of it whichever order it arrives in, and commits arriving
    mid-walk fall outside the frozen bound and wait for the next pass.
    """

    since: str | None = None
    until: str | None = None
    page: int = 1


class GitHubCommitAdapter:
    """Read one branch of one repository through the GitHub MCP server.

    ``repository_id`` rather than ``owner/name`` builds the replay key. The key
    is compared as a string, so anything that can be spelled two ways for a
    single repository breaks replay protection: GitHub treats ``owner/name`` as
    case-insensitive, and it changes on rename or transfer. The numeric id does
    neither. The readable name is kept in the payload, where it is evidence for
    people rather than an identity the pipeline compares.

    The branch is part of the key as well. The backend compares the whole
    payload on replay and permanently rejects a matching identity carrying
    different data, so the identity has to partition at least as finely as the
    payload varies — and the payload's ``ref`` is per branch. Watching one
    commit from two branches is therefore two observations rather than one
    silent rejection.
    """

    tool_name = "list_commits"

    def __init__(
        self,
        *,
        repository_id: int,
        repository: str,
        branch: str = "main",
        page_size: int = 50,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        owner, separator, name = repository.strip().partition("/")
        if not separator or not owner.strip() or not name.strip():
            raise ValueError(f"repository must be owner/name, got {repository!r}")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")

        self._repository_id = repository_id
        # Lowercased because this reaches the payload, which the backend
        # compares byte for byte on replay. GitHub treats the name as
        # case-insensitive, so two spellings of one repository would otherwise
        # make the same commit look like different observation data and be
        # rejected permanently.
        self._repository = f"{owner.strip()}/{name.strip()}".lower()
        self._owner = owner.strip()
        self._name = name.strip()
        self._branch = branch.strip()
        self._page_size = page_size
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Sampled while building the baseline request, consumed when normalizing
        # its response. One adapter serves one pass, so the two always pair.
        self._baseline_boundary: datetime | None = None

    def build_arguments(self, position: str | None) -> dict[str, Any]:
        window = _read_window(position)
        if position is None:
            # Before the read, not after it. A commit created between GitHub
            # building the response and the clock being sampled would otherwise
            # be missing from what was read and older than the stored bound, so
            # the next pass would skip it permanently. Taking the boundary first
            # makes the worst case a re-read, which deduplicates.
            self._baseline_boundary = self._now()
        arguments: dict[str, Any] = {
            "owner": self._owner,
            "repo": self._name,
            "sha": self._branch,
            "perPage": self._page_size,
        }
        if window.since:
            arguments["since"] = window.since
        if window.until:
            arguments["until"] = window.until
            arguments["page"] = window.page
        return arguments

    def normalize(self, raw: dict[str, Any], position: str | None) -> AdapterResult:
        window = _read_window(position)
        commits = _commits_in(raw)

        if position is None:
            # Connecting a repository takes a baseline rather than replaying its
            # history: those commits predate the architecture they would be
            # evaluated against. Reading the clock rather than the newest commit
            # returned also avoids assuming which end of the listing GitHub
            # sends first, which nothing in its contract promises.
            boundary = self._baseline_boundary or self._now()
            self._baseline_boundary = None
            return AdapterResult(signals=[], next_position=boundary.isoformat())

        signals: list[NormalizedSignal] = []
        dated: list[tuple[datetime, str]] = []

        for commit in commits:
            sha = str(commit.get("sha") or "").strip()
            if not sha:
                raise ValueError("GitHub commit is missing its sha")

            authored = commit.get("commit") or {}
            author = authored.get("author") or {}
            raw_date = str(author.get("date") or "").strip()
            occurred_at = _parse_timestamp(raw_date)

            payload = GitHubChangePayload(
                repository=self._repository,
                event_kind=GitHubChangeKind.PUSH,
                summary=_summary_of(authored.get("message"), sha),
                ref=f"refs/heads/{self._branch}",
                commit_sha=sha,
                actor=_actor_of(commit),
                commits=[sha],
            )
            signals.append(
                NormalizedSignal(
                    # Deterministic: the same commit read from the same branch
                    # always yields this identity, and the payload behind it.
                    source_event_id=(
                        f"github:{self._repository_id}:{self._branch}:commit:{sha}"
                    ),
                    source=ProjectEventSource.GITHUB,
                    event_type=ProjectEventType.GITHUB_CHANGE,
                    payload=payload.model_dump(mode="json", exclude_none=True),
                    occurred_at=occurred_at,
                )
            )

            if occurred_at is not None:
                dated.append((occurred_at, raw_date))

        advanced = self._advance(window, dated, len(commits))
        # ``None`` means keep the stored position. Writing back a value the
        # cursor already holds is a needless round trip on every quiet pass.
        return AdapterResult(
            signals=signals,
            next_position=None if advanced == position else advanced,
        )

    def _advance(
        self,
        window: _Window,
        dated: list[tuple[datetime, str]],
        page_length: int,
    ) -> str | None:
        if page_length < self._page_size:
            # Nothing was truncated, so everything in the window has been seen.
            if window.until:
                return window.until
            newest = max(dated, default=None)
            return _later_of(window.since, newest[1] if newest else None)

        if window.until is None:
            # Freeze the top of the window and restart at its first page: the
            # tighter bound can change where page boundaries fall, and assuming
            # otherwise would smuggle the ordering assumption back in.
            return json.dumps(
                {"since": window.since, "until": self._now().isoformat(), "page": 1},
                sort_keys=True,
            )
        return json.dumps(
            {"since": window.since, "until": window.until, "page": window.page + 1},
            sort_keys=True,
        )


def _read_window(position: str | None) -> _Window:
    if not position:
        return _Window()
    text = position.strip()
    if not text.startswith("{"):
        # Positions written before windowing existed are a bare lower bound.
        return _Window(since=text)
    try:
        state = json.loads(text)
    except ValueError:
        return _Window(since=text)
    if not isinstance(state, dict):
        return _Window(since=text)
    page = state.get("page")
    return _Window(
        since=_text_or_none(state.get("since")),
        until=_text_or_none(state.get("until")),
        page=page if isinstance(page, int) and page >= 1 else 1,
    )


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _later_of(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    left_at, right_at = _parse_timestamp(left), _parse_timestamp(right)
    if left_at is None:
        return right
    if right_at is None:
        return left
    return right if right_at > left_at else left


def _commits_in(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the commit list out of whichever shape the MCP server returned.

    Anything unreadable raises. Returning an empty list instead would be
    indistinguishable from a quiet repository: the cursor would hold, the pass
    would report success, and an expired token would look like no news.
    """
    if raw.get("isError"):
        raise ValueError(f"GitHub MCP tool reported an error: {_error_text(raw)}")

    commits = _commit_list(raw.get("structuredContent"))
    if commits is not None:
        return commits

    for block in raw.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            decoded = json.loads(block.get("text") or "")
        except (TypeError, ValueError):
            continue
        commits = _commit_list(decoded)
        if commits is not None:
            return commits

    commits = _commit_list(raw)
    if commits is None:
        raise ValueError(f"GitHub MCP result could not be read as commits: {_shape_of(raw)}")
    return commits


def _error_text(raw: dict[str, Any]) -> str:
    for block in raw.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                return text
    return "no detail provided"


def _shape_of(raw: dict[str, Any]) -> str:
    return ", ".join(sorted(str(key) for key in raw)) or "empty result"


def _commit_list(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    if isinstance(value, dict) and isinstance(value.get("commits"), list):
        return [entry for entry in value["commits"] if isinstance(entry, dict)]
    return None


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summary_of(message: Any, sha: str) -> str:
    first_line = str(message or "").strip().split("\n", 1)[0].strip()
    # The contract refuses an empty summary, and an empty commit message is
    # legal on GitHub. Fall back to something that still identifies the commit.
    return first_line or f"Commit {sha}"


def _actor_of(commit: dict[str, Any]) -> str | None:
    account = commit.get("author")
    if isinstance(account, dict):
        login = str(account.get("login") or "").strip()
        if login:
            return login
    authored = commit.get("commit") or {}
    author = authored.get("author") or {}
    return str(author.get("name") or "").strip() or None
