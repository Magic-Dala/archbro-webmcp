"""Composition root for the GitHub connector.

The pipeline was complete but unreachable: nothing in the product constructed a
``ConnectorSync``. This composes one, and lives in ``runtime/`` because that is
the only layer allowed to choose concrete persistence and model providers — the
pipeline package is guarded against importing them.

Run one pass with ``python -m archbro.platform.runtime.connector_sync``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from functools import partial
from typing import Any, Mapping

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.gemini import GeminiProvider
from archbro.backend.mcp.gateway import ConnectedMcpGateway
from archbro.integrations.github.adapter import GitHubCommitAdapter
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.pipeline.cursor import PostgresSyncCursorStore
from archbro.platform.pipeline.delivery import SignalDelivery
from archbro.platform.pipeline.runner import ConnectorRun, Syncable, run_connectors
from archbro.platform.pipeline.sync import ConnectorSync

logger = logging.getLogger("archbro.pipeline")

CONNECTORS_ENV = "ARCHBRO_GITHUB_CONNECTORS_JSON"

_REQUIRED_FIELDS = ("project_id", "connector_id", "repository_id", "repository")


@dataclass(frozen=True, slots=True)
class GitHubConnectorSettings:
    """One repository branch watched for one project.

    ``connector_id`` is also the MCP server id the gateway reads from, so it must
    match an entry in ``ARCHBRO_MCP_SERVERS_JSON``.
    """

    project_id: str
    connector_id: str
    repository_id: int
    repository: str
    branch: str = "main"


@dataclass(frozen=True, slots=True)
class ConnectorConfiguration:
    """Connectors that parsed, and the entries that did not.

    A malformed entry is a failure of that connector, not of the batch: parsing
    them all up front means one typo silences every other repository for the
    pass, and the worker's retry only repeats it.
    """

    connectors: list[GitHubConnectorSettings]
    errors: list[ValueError]


def github_connectors_from_env(
    env: Mapping[str, str] | None = None,
) -> ConnectorConfiguration:
    source = os.environ if env is None else env
    raw = (source.get(CONNECTORS_ENV) or "").strip()
    if not raw:
        return ConnectorConfiguration(connectors=[], errors=[])

    # These two are properties of the variable rather than of one connector,
    # so there is nothing to isolate: no entry can be read at all.
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{CONNECTORS_ENV} is invalid JSON") from exc
    if not isinstance(entries, list):
        raise ValueError(f"{CONNECTORS_ENV} must be a JSON list of connectors")

    connectors: list[GitHubConnectorSettings] = []
    errors: list[ValueError] = []
    for index, entry in enumerate(entries):
        try:
            connectors.append(_settings_from(entry, index))
        except ValueError as exc:
            logger.warning("connector_configuration_rejected error=%s", exc)
            errors.append(exc)
    return ConnectorConfiguration(connectors=connectors, errors=errors)


def _settings_from(entry: Any, index: int) -> GitHubConnectorSettings:
    where = f"{CONNECTORS_ENV}[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be an object")

    for field in _REQUIRED_FIELDS:
        value = entry.get(field)
        # Trim before the check, not after: "   " would otherwise pass as
        # present and become empty, surfacing later as an obscure missing
        # project or MCP server rather than a configuration error.
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{where} is missing {field}")

    repository_id = entry["repository_id"]
    # A quoted id still formats into a replay key, so accepting one would not
    # fail until the key silently differed from every key written before it.
    if isinstance(repository_id, bool) or not isinstance(repository_id, int):
        raise ValueError(f"{where} repository_id must be a number, not a string")

    return GitHubConnectorSettings(
        project_id=str(entry["project_id"]).strip(),
        connector_id=str(entry["connector_id"]).strip(),
        repository_id=repository_id,
        repository=str(entry["repository"]).strip(),
        branch=str(entry.get("branch") or "main").strip() or "main",
    )


@dataclass(frozen=True, slots=True)
class _CursorIdentity:
    project_id: str
    connector_id: str


def sync_source_id(settings: GitHubConnectorSettings) -> str:
    """The cursor key for one watched branch.

    Distinct from ``connector_id``, which names the MCP server: several
    repositories and branches are read through one GitHub server, and a shared
    cursor would let them overwrite each other's position.
    """
    return f"github:{settings.repository_id}:{settings.branch}"


def build_github_sync(settings: GitHubConnectorSettings, *, dsn: str) -> Syncable:
    repository = PostgresProjectRepository(dsn)
    provider_name = (
        os.getenv("ARCHBRO_PROVIDER") or os.getenv("HUMAN_AGENT_PROVIDER") or "gemini"
    ).lower()
    provider = (
        FakeModelProvider()
        if provider_name == "fake"
        else GeminiProvider(model_id=os.getenv("GEMINI_MODEL", "gemini-3.7-flash"))
    )

    return ConnectorSync(
        gateway=ConnectedMcpGateway.from_env(),
        adapter=GitHubCommitAdapter(
            repository_id=settings.repository_id,
            repository=settings.repository,
            branch=settings.branch,
        ),
        delivery=SignalDelivery(AgentOrchestrator(repository, provider)),
        cursor_store=PostgresSyncCursorStore(dsn),
        server_id=settings.connector_id,
    )


async def run_once() -> list[ConnectorRun]:
    configuration = github_connectors_from_env()
    # Rejected entries are reported alongside the runs rather than dropped, so a
    # typo is visible without having stopped the connectors that did parse.
    rejected = [
        ConnectorRun(settings=_CursorIdentity("", CONNECTORS_ENV), error=error)
        for error in configuration.errors
    ]
    if not configuration.connectors:
        if not rejected:
            logger.info("connector_run_skipped reason=no_connectors_configured")
        return rejected

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise ValueError("DATABASE_URL is required to run connector syncs")

    runs = await run_connectors(
        [
            (
                _CursorIdentity(settings.project_id, sync_source_id(settings)),
                partial(build_github_sync, settings, dsn=dsn),
            )
            for settings in configuration.connectors
        ]
    )
    return rejected + runs


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    runs = asyncio.run(run_once())
    return 1 if any(run.error is not None for run in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
