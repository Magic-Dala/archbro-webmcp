from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from archbro.backend.core.contracts import AgentRunResult, ProjectEvent
from archbro.backend.core.observation import (
    ObservationInProgressError,
    ObservationRejectedError,
)
from archbro.platform.pipeline.contracts import (
    DeliveryOutcome,
    DeliveryResult,
    NormalizedSignal,
)

logger = logging.getLogger("archbro.pipeline")


class ObservationSink(Protocol):
    async def observe_event(self, event: ProjectEvent) -> AgentRunResult: ...


Sleeper = Callable[[float], Awaitable[None]]


class SignalDelivery:
    """Deliver one normalized signal into the Agent observation boundary.

    Two failure modes are deliberately kept apart. A transport failure belongs to
    the connector and is retried by re-reading the source. A concurrency conflict
    means another worker already holds this observation, so the same signal is
    retried here without touching the external source again.
    """

    def __init__(
        self,
        sink: ObservationSink,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Sleeper | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")
        self._sink = sink
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep or asyncio.sleep

    async def deliver(self, project_id: str, signal: NormalizedSignal) -> DeliveryResult:
        event = ProjectEvent(
            project_id=project_id,
            type=signal.event_type,
            source=signal.source,
            source_event_id=signal.source_event_id,
            occurred_at=signal.occurred_at,
            payload=signal.payload,
        )

        for attempt in range(1, self._max_attempts + 1):
            try:
                run = await self._sink.observe_event(event)
            except ObservationRejectedError as exc:
                logger.warning(
                    "signal_delivery_rejected project_id=%s source_event_id=%s reason=%s",
                    project_id,
                    signal.source_event_id,
                    exc,
                )
                return DeliveryResult(outcome=DeliveryOutcome.REJECTED, signal=signal)
            except ObservationInProgressError:
                if attempt == self._max_attempts:
                    logger.info(
                        "signal_delivery_conflict project_id=%s source_event_id=%s attempts=%s",
                        project_id,
                        signal.source_event_id,
                        attempt,
                    )
                    return DeliveryResult(outcome=DeliveryOutcome.CONFLICT, signal=signal)
                await self._sleep(self._backoff_seconds * attempt)
                continue

            if run.result == "ERROR":
                outcome = DeliveryOutcome.FAILED
            elif run.replayed:
                outcome = DeliveryOutcome.REPLAYED
            else:
                outcome = DeliveryOutcome.APPLIED
            return DeliveryResult(outcome=outcome, signal=signal, run=run)

        raise AssertionError("unreachable: delivery loop must return")
