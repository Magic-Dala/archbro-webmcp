"""One connector sync run: read the source, deliver signals, advance the cursor.

The pipeline stays provider-agnostic. It asks the adapter which tool to call and
how to resume, hands the raw result straight back, and never inspects payloads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

import pytest

from archbro.backend.core.contracts import ProjectEventSource, ProjectEventType
from archbro.platform.pipeline.contracts import (
    AdapterResult,
    DeliveryOutcome,
    DeliveryResult,
    NormalizedSignal,
    SyncCursor,
)
from archbro.platform.pipeline.cursor import PostgresSyncCursorStore
from conftest import requires_database
from archbro.platform.pipeline.sync import ConnectorSync

pytestmark = requires_database


def _signal(commit_sha: str) -> NormalizedSignal:
    return NormalizedSignal(
        source_event_id=commit_sha,
        source=ProjectEventSource.GITHUB,
        event_type=ProjectEventType.GITHUB_CHANGE,
        payload={"commit_sha": commit_sha, "summary": "changed"},
    )


class _FakeAdapter:
    """Stands in for Ayushi's provider-specific adapter."""

    tool_name = "list_commits"

    def __init__(self, signals: list[NormalizedSignal], next_position: str | None) -> None:
        self._signals = signals
        self._next_position = next_position
        self.seen_positions: list[str | None] = []

    def build_arguments(self, position: str | None) -> dict:
        self.seen_positions.append(position)
        return {"since": position} if position else {}

    def normalize(self, raw: dict, position: str | None) -> AdapterResult:
        return AdapterResult(signals=list(self._signals), next_position=self._next_position)


class _FakeGateway:
    def __init__(self, raw: dict | None = None, error: Exception | None = None) -> None:
        self._raw = raw or {"commits": []}
        self._error = error
        self.calls: list[tuple[str, str, str, dict]] = []

    async def call_tool(self, project_id, server_id, tool_name, arguments):
        self.calls.append((project_id, server_id, tool_name, arguments))
        if self._error is not None:
            raise self._error
        return self._raw


class _FakeDelivery:
    def __init__(self, outcomes: list[DeliveryOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.delivered: list[NormalizedSignal] = []

    async def deliver(self, project_id: str, signal: NormalizedSignal) -> DeliveryResult:
        self.delivered.append(signal)
        outcome = self._outcomes.pop(0) if self._outcomes else DeliveryOutcome.APPLIED
        return DeliveryResult(outcome=outcome, signal=signal)


def _cursor_store(dsn: str) -> PostgresSyncCursorStore:
    return PostgresSyncCursorStore(dsn)


def test_first_sync_starts_from_no_position_and_records_where_it_stopped(dsn):
    adapter = _FakeAdapter([_signal("aaa"), _signal("bbb")], next_position="2026-08-28T12:00:00+00:00")
    gateway = _FakeGateway()
    delivery = _FakeDelivery([DeliveryOutcome.APPLIED, DeliveryOutcome.APPLIED])
    store = _cursor_store(dsn)

    sync = ConnectorSync(gateway=gateway, adapter=adapter, delivery=delivery, cursor_store=store)
    report = asyncio.run(sync.sync("proj_1", "github", owner_user_id="uid-alice"))

    assert adapter.seen_positions == [None]
    assert gateway.calls == [("proj_1", "github", "list_commits", {})]
    assert [signal.source_event_id for signal in delivery.delivered] == ["aaa", "bbb"]

    assert report.applied == 2
    assert report.advanced is True

    saved = store.load("proj_1", "github")
    assert saved is not None
    assert saved.position == "2026-08-28T12:00:00+00:00"
    assert saved.owner_user_id == "uid-alice"


def test_next_sync_resumes_from_the_stored_position(dsn):
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="2026-08-27T00:00:00+00:00"))

    adapter = _FakeAdapter([_signal("ccc")], next_position="2026-08-28T00:00:00+00:00")
    gateway = _FakeGateway()
    sync = ConnectorSync(
        gateway=gateway,
        adapter=adapter,
        delivery=_FakeDelivery([DeliveryOutcome.APPLIED]),
        cursor_store=store,
    )

    asyncio.run(sync.sync("proj_1", "github"))

    assert adapter.seen_positions == ["2026-08-27T00:00:00+00:00"]
    assert gateway.calls[0][3] == {"since": "2026-08-27T00:00:00+00:00"}
    assert store.load("proj_1", "github").position == "2026-08-28T00:00:00+00:00"


def test_replayed_signals_still_allow_the_cursor_to_advance(dsn):
    store = _cursor_store(dsn)
    adapter = _FakeAdapter([_signal("aaa")], next_position="later")
    sync = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=adapter,
        delivery=_FakeDelivery([DeliveryOutcome.REPLAYED]),
        cursor_store=store,
    )

    report = asyncio.run(sync.sync("proj_1", "github"))

    assert report.replayed == 1
    assert report.advanced is True
    assert store.load("proj_1", "github").position == "later"


def test_unfinished_signal_holds_the_cursor_back_so_the_next_sync_retries(dsn):
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    adapter = _FakeAdapter([_signal("aaa"), _signal("bbb")], next_position="later")
    sync = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=adapter,
        delivery=_FakeDelivery([DeliveryOutcome.APPLIED, DeliveryOutcome.CONFLICT]),
        cursor_store=store,
    )

    report = asyncio.run(sync.sync("proj_1", "github"))

    assert report.applied == 1
    assert report.conflicts == 1
    assert report.advanced is False
    assert store.load("proj_1", "github").position == "start"


def test_rejected_signal_does_not_deadlock_the_connector(dsn):
    """A rejected signal is permanent, so it must not hold the cursor forever.

    Holding back on a contract violation would block every later observation for
    that connector with no way out. The count is surfaced in the report instead,
    and recovering the lost window means fixing the adapter and rewinding the
    cursor deliberately.
    """
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    adapter = _FakeAdapter([_signal("aaa"), _signal("bbb")], next_position="later")
    sync = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=adapter,
        delivery=_FakeDelivery([DeliveryOutcome.REJECTED, DeliveryOutcome.APPLIED]),
        cursor_store=store,
    )

    report = asyncio.run(sync.sync("proj_1", "github"))

    assert report.rejected == 1
    assert report.applied == 1
    assert report.advanced is True
    assert store.load("proj_1", "github").position == "later"


def test_a_connector_that_never_finishes_is_reported_as_stalled(dsn):
    """Holding the cursor back is correct, but it must not fail silently.

    A signal that can never succeed — a payload the backend refuses before it is
    even registered — would otherwise block every later observation for this
    connector with nothing surfacing it.
    """
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    def run_once():
        sync = ConnectorSync(
            gateway=_FakeGateway(),
            adapter=_FakeAdapter([_signal("aaa")], next_position="later"),
            delivery=_FakeDelivery([DeliveryOutcome.FAILED]),
            cursor_store=store,
            stall_threshold=3,
        )
        return asyncio.run(sync.sync("proj_1", "github"))

    assert run_once().stalled is False
    assert run_once().stalled is False
    third = run_once()

    assert third.stalled is True
    assert third.stalled_attempts == 3
    assert store.load("proj_1", "github").position == "start"


def test_a_successful_pass_clears_the_stall_counter(dsn):
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    failing = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=_FakeAdapter([_signal("aaa")], next_position="later"),
        delivery=_FakeDelivery([DeliveryOutcome.FAILED]),
        cursor_store=store,
        stall_threshold=2,
    )
    asyncio.run(failing.sync("proj_1", "github"))

    healthy = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=_FakeAdapter([_signal("bbb")], next_position="later"),
        delivery=_FakeDelivery([DeliveryOutcome.APPLIED]),
        cursor_store=store,
        stall_threshold=2,
    )
    report = asyncio.run(healthy.sync("proj_1", "github"))

    assert report.advanced is True
    assert report.stalled_attempts == 0
    assert store.load("proj_1", "github").stalled_attempts == 0


def test_transport_failure_leaves_the_cursor_untouched(dsn):
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    gateway = _FakeGateway(error=RuntimeError("MCP endpoint unreachable"))
    sync = ConnectorSync(
        gateway=gateway,
        adapter=_FakeAdapter([], next_position="later"),
        delivery=_FakeDelivery([]),
        cursor_store=store,
    )

    with pytest.raises(RuntimeError, match="MCP endpoint unreachable"):
        asyncio.run(sync.sync("proj_1", "github"))

    assert store.load("proj_1", "github").position == "start"


def test_empty_result_does_not_lose_the_existing_position(dsn):
    store = _cursor_store(dsn)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    sync = ConnectorSync(
        gateway=_FakeGateway(),
        adapter=_FakeAdapter([], next_position=None),
        delivery=_FakeDelivery([]),
        cursor_store=store,
    )

    report = asyncio.run(sync.sync("proj_1", "github"))

    assert report.applied == 0
    assert store.load("proj_1", "github").position == "start"
