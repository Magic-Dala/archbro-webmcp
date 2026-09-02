"""Run connector sync passes, isolating each connector from the others.

Deliberately provider-agnostic and free of persistence imports, like the rest of
this package: choosing concrete repositories, model providers, and adapters is
the runtime composition root's job, not the pipeline's. See
``archbro.platform.runtime.connector_sync`` for the GitHub composition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from archbro.platform.pipeline.sync import SyncReport

logger = logging.getLogger("archbro.pipeline")


class ConnectorIdentity(Protocol):
    """Whatever names a connector; the pipeline needs nothing more from it."""

    project_id: str
    connector_id: str


class Syncable(Protocol):
    async def sync(
        self, project_id: str, connector_id: str, **kwargs: Any
    ) -> SyncReport: ...


@dataclass(frozen=True, slots=True)
class ConnectorRun:
    """What one connector did, including the reason it did nothing."""

    settings: ConnectorIdentity
    report: SyncReport | None = None
    error: Exception | None = None


async def run_connectors(
    plans: Sequence[tuple[ConnectorIdentity, Callable[[], Syncable]]],
) -> list[ConnectorRun]:
    """Run one pass per connector.

    Connectors are independent sources. A repository whose token expired must
    not stop every other project from observing its own changes, so a failure is
    recorded against its connector and the loop continues.

    Each connector is built inside its own guard rather than up front:
    construction reads configuration and opens resources, so it fails for the
    same reasons syncing does, and building them all first would let one bad
    entry silence the whole pass.
    """
    runs: list[ConnectorRun] = []
    for settings, build in plans:
        try:
            sync = build()
            report = await sync.sync(settings.project_id, settings.connector_id)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            logger.warning(
                "connector_run_failed project_id=%s connector_id=%s error=%s",
                settings.project_id,
                settings.connector_id,
                exc,
            )
            runs.append(ConnectorRun(settings=settings, error=exc))
            continue

        logger.info(
            "connector_run_completed project_id=%s connector_id=%s applied=%s "
            "replayed=%s conflicts=%s failed=%s rejected=%s stalled=%s",
            settings.project_id,
            settings.connector_id,
            report.applied,
            report.replayed,
            report.conflicts,
            report.failed,
            report.rejected,
            report.stalled,
        )
        runs.append(ConnectorRun(settings=settings, report=report))
    return runs
