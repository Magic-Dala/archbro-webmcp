from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from archbro.platform.pipeline.contracts import (
    DeliveryOutcome,
    DeliveryResult,
    NormalizedSignal,
    SignalAdapter,
    SyncCursor,
    SyncCursorStore,
)

logger = logging.getLogger("archbro.pipeline")


class McpToolCaller(Protocol):
    async def call_tool(
        self,
        project_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class SignalDeliverer(Protocol):
    async def deliver(self, project_id: str, signal: NormalizedSignal) -> DeliveryResult: ...


@dataclass(frozen=True, slots=True)
class SyncReport:
    project_id: str
    connector_id: str
    applied: int = 0
    replayed: int = 0
    conflicts: int = 0
    failed: int = 0
    rejected: int = 0
    advanced: bool = False
    #: Consecutive passes that ended with work still needing redelivery.
    stalled_attempts: int = 0
    #: The connector has not made progress for ``stall_threshold`` passes.
    stalled: bool = False

    @property
    def unfinished(self) -> int:
        """Signals worth re-reading. Rejections are permanent and excluded."""
        return self.conflicts + self.failed


class ConnectorSync:
    """Run one read-and-deliver pass for a single connector.

    The cursor only advances when every signal in the batch reached a terminal
    Agent outcome. A conflicted or failed signal holds the position back so the
    next pass re-reads the same window; redelivery is safe because the Agent
    deduplicates on ``source_event_id``.
    """

    def __init__(
        self,
        *,
        gateway: McpToolCaller,
        adapter: SignalAdapter,
        delivery: SignalDeliverer,
        cursor_store: SyncCursorStore,
        stall_threshold: int = 5,
        server_id: str | None = None,
    ) -> None:
        if stall_threshold < 1:
            raise ValueError("stall_threshold must be at least 1")
        # One MCP server can feed many sources. The cursor is keyed by
        # connector_id, so reusing it as the server id makes two repositories
        # read through the same server share a cursor and overwrite each
        # other's position. Defaults to the connector for single-source servers.
        self._server_id = server_id
        self._gateway = gateway
        self._adapter = adapter
        self._delivery = delivery
        self._cursor_store = cursor_store
        self._stall_threshold = stall_threshold

    async def sync(
        self,
        project_id: str,
        connector_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> SyncReport:
        existing = self._cursor_store.load(project_id, connector_id)
        position = existing.position if existing is not None else None
        acting_user_id = owner_user_id or (existing.owner_user_id if existing else None)

        arguments = self._adapter.build_arguments(position)
        raw = await self._gateway.call_tool(
            project_id,
            self._server_id or connector_id,
            self._adapter.tool_name,
            arguments,
        )
        result = self._adapter.normalize(raw, position)

        counts = {outcome: 0 for outcome in DeliveryOutcome}
        for signal in result.signals:
            delivered = await self._delivery.deliver(project_id, signal)
            counts[delivered.outcome] += 1

        unfinished = counts[DeliveryOutcome.CONFLICT] + counts[DeliveryOutcome.FAILED]
        previous_attempts = existing.stalled_attempts if existing is not None else 0
        advanced = False
        stalled_attempts = 0

        if unfinished:
            # Holding the position back is correct — the same window must be
            # re-read — but a signal the backend can never accept would block
            # every later observation for this connector. Count the passes so the
            # stall surfaces instead of failing silently.
            stalled_attempts = previous_attempts + 1
            self._cursor_store.record_stall(project_id, connector_id, stalled_attempts)
        elif result.next_position is not None:
            advanced = self._cursor_store.advance(
                project_id,
                connector_id,
                expected_position=position,
                position=result.next_position,
                owner_user_id=acting_user_id,
            )
            if not advanced:
                # Another worker moved the position while this pass was running.
                # Its window is at least as new as ours, so losing is harmless.
                logger.info(
                    "connector_sync_position_superseded project_id=%s connector_id=%s",
                    project_id,
                    connector_id,
                )

        report = SyncReport(
            project_id=project_id,
            connector_id=connector_id,
            applied=counts[DeliveryOutcome.APPLIED],
            replayed=counts[DeliveryOutcome.REPLAYED],
            conflicts=counts[DeliveryOutcome.CONFLICT],
            failed=counts[DeliveryOutcome.FAILED],
            rejected=counts[DeliveryOutcome.REJECTED],
            advanced=advanced,
            stalled_attempts=stalled_attempts,
            stalled=stalled_attempts >= self._stall_threshold,
        )
        log = logger.warning if (report.rejected or report.stalled) else logger.info
        log(
            "connector_sync project_id=%s connector_id=%s applied=%s replayed=%s "
            "conflicts=%s failed=%s rejected=%s advanced=%s stalled=%s",
            project_id,
            connector_id,
            report.applied,
            report.replayed,
            report.conflicts,
            report.failed,
            report.rejected,
            report.advanced,
            report.stalled,
        )
        return report
