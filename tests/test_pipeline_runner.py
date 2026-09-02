"""Runtime wiring for the pull-based connector pipeline.

The pipeline itself was already complete and tested; nothing constructed it.
This covers the part that turns deployment configuration into a running sync
pass, which is where a misconfiguration would otherwise surface as silence.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from archbro.platform.pipeline.runner import run_connectors
from archbro.platform.pipeline.sync import SyncReport
from archbro.platform.runtime.connector_sync import (
    GitHubConnectorSettings,
    github_connectors_from_env,
)


VALID = (
    '[{"project_id":"proj_1","connector_id":"github","repository_id":987654321,'
    '"repository":"Magic-Dala/archbro","branch":"main"}]'
)


def test_no_configuration_means_no_connectors():
    assert github_connectors_from_env({}).connectors == []
    assert github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": "  "}).connectors == []


def test_a_configured_connector_is_read_in_full():
    (settings,) = github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": VALID}).connectors

    assert settings == GitHubConnectorSettings(
        project_id="proj_1",
        connector_id="github",
        repository_id=987654321,
        repository="Magic-Dala/archbro",
        branch="main",
    )


def test_the_branch_defaults_when_it_is_not_configured():
    raw = (
        '[{"project_id":"proj_1","connector_id":"github","repository_id":1,'
        '"repository":"Magic-Dala/archbro"}]'
    )

    (settings,) = github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": raw}).connectors

    assert settings.branch == "main"


def test_invalid_json_names_the_variable_rather_than_failing_obscurely():
    with pytest.raises(ValueError, match="ARCHBRO_GITHUB_CONNECTORS_JSON"):
        github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": "{not json"})


def test_configuration_must_be_a_list_of_connectors():
    with pytest.raises(ValueError, match="list"):
        github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": '{"project_id":"p"}'})


@pytest.mark.parametrize(
    "missing",
    ["project_id", "connector_id", "repository_id", "repository"],
)
def test_a_missing_required_field_is_named(missing: str):
    entry = {
        "project_id": "proj_1",
        "connector_id": "github",
        "repository_id": 1,
        "repository": "Magic-Dala/archbro",
    }
    entry.pop(missing)

    configuration = github_connectors_from_env(
        {"ARCHBRO_GITHUB_CONNECTORS_JSON": json.dumps([entry])}
    )

    assert configuration.connectors == []
    assert missing in str(configuration.errors[0])


def test_a_repository_id_that_is_not_a_number_is_refused():
    # A quoted id still formats into a replay key, so it would not fail until
    # the key silently differed from every key written before it.
    raw = (
        '[{"project_id":"proj_1","connector_id":"github","repository_id":"987654321",'
        '"repository":"Magic-Dala/archbro"}]'
    )

    configuration = github_connectors_from_env({"ARCHBRO_GITHUB_CONNECTORS_JSON": raw})

    assert configuration.connectors == []
    assert "repository_id" in str(configuration.errors[0])


class _StubSync:
    def __init__(self, report: SyncReport | None = None, error: Exception | None = None):
        self._report = report
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def sync(self, project_id: str, connector_id: str, **_: object) -> SyncReport:
        self.calls.append((project_id, connector_id))
        if self._error:
            raise self._error
        assert self._report is not None
        return self._report


def _settings(project_id: str = "proj_1", connector_id: str = "github"):
    return GitHubConnectorSettings(
        project_id=project_id,
        connector_id=connector_id,
        repository_id=1,
        repository="Magic-Dala/archbro",
    )


def test_each_connector_is_synced_with_its_own_identifiers():
    report = SyncReport(project_id="proj_1", connector_id="github", applied=2)
    sync = _StubSync(report)

    results = asyncio.run(run_connectors([(_settings(), lambda: sync)]))

    assert sync.calls == [("proj_1", "github")]
    assert [result.report for result in results] == [report]
    assert results[0].error is None


def test_one_failing_connector_does_not_stop_the_others():
    # Connectors are independent sources. A repository whose token expired must
    # not stop every other project from observing its own changes.
    broken = _StubSync(error=RuntimeError("token expired"))
    working = _StubSync(SyncReport(project_id="proj_2", connector_id="github", applied=1))

    results = asyncio.run(
        run_connectors(
            [
                (_settings(project_id="proj_1"), lambda: broken),
                (_settings(project_id="proj_2"), lambda: working),
            ]
        )
    )

    assert results[0].report is None
    assert isinstance(results[0].error, RuntimeError)
    assert results[1].report is not None
    assert working.calls == [("proj_2", "github")]


@pytest.mark.parametrize("blank", ["project_id", "connector_id", "repository"])
def test_an_identifier_that_is_only_whitespace_is_refused(blank: str):
    # Checked before trimming, "   " passes as present and then becomes empty,
    # surfacing later as an obscure missing project or MCP server.
    entry = {
        "project_id": "proj_1",
        "connector_id": "github",
        "repository_id": 1,
        "repository": "Magic-Dala/archbro",
    }
    entry[blank] = "   "

    configuration = github_connectors_from_env(
        {"ARCHBRO_GITHUB_CONNECTORS_JSON": json.dumps([entry])}
    )

    assert configuration.connectors == []
    assert blank in str(configuration.errors[0])


def test_two_repositories_on_one_mcp_server_get_separate_cursors():
    from archbro.platform.runtime.connector_sync import sync_source_id

    first = GitHubConnectorSettings(
        project_id="proj_1",
        connector_id="github",
        repository_id=111,
        repository="Magic-Dala/archbro",
    )
    second = GitHubConnectorSettings(
        project_id="proj_1",
        connector_id="github",
        repository_id=222,
        repository="Magic-Dala/other",
    )

    assert sync_source_id(first) != sync_source_id(second)


def test_two_branches_of_one_repository_get_separate_cursors():
    from archbro.platform.runtime.connector_sync import sync_source_id

    main = GitHubConnectorSettings(
        project_id="proj_1",
        connector_id="github",
        repository_id=111,
        repository="Magic-Dala/archbro",
        branch="main",
    )
    release = GitHubConnectorSettings(
        project_id="proj_1",
        connector_id="github",
        repository_id=111,
        repository="Magic-Dala/archbro",
        branch="release",
    )

    assert sync_source_id(main) != sync_source_id(release)


def test_a_connector_that_cannot_be_built_does_not_stop_the_others():
    # Construction reads configuration and opens resources, so it fails for the
    # same reasons syncing does. Building every connector before running any
    # would let one bad entry silence the whole pass.
    working = _StubSync(SyncReport(project_id="proj_2", connector_id="github", applied=1))

    def broken() -> _StubSync:
        raise ValueError("no such MCP server")

    results = asyncio.run(
        run_connectors(
            [
                (_settings(project_id="proj_1"), broken),
                (_settings(project_id="proj_2"), lambda: working),
            ]
        )
    )

    assert isinstance(results[0].error, ValueError)
    assert results[1].report is not None


def test_one_malformed_entry_does_not_hide_the_valid_ones_around_it():
    # Parsing every entry up front means a single typo silences every other
    # repository for that pass, and the worker's retry only repeats it.
    entries = [
        {
            "project_id": "proj_1",
            "connector_id": "github",
            "repository_id": 111,
            "repository": "Magic-Dala/first",
        },
        {"project_id": "proj_1", "connector_id": "github"},
        {
            "project_id": "proj_1",
            "connector_id": "github",
            "repository_id": 333,
            "repository": "Magic-Dala/third",
        },
    ]

    configuration = github_connectors_from_env(
        {"ARCHBRO_GITHUB_CONNECTORS_JSON": json.dumps(entries)}
    )

    assert [c.repository_id for c in configuration.connectors] == [111, 333]
    assert len(configuration.errors) == 1
    assert "[1]" in str(configuration.errors[0])


def test_ingestion_never_reaches_the_per_user_provider_gateway():
    """Two credential models exist and must not meet.

    ExternalMcpGateway holds GitHub OAuth connections bound to one
    TrustedPrincipal and kept in the app process's memory. This worker runs in
    its own container against deployment-configured credentials. Reaching for
    the per-user gateway here would either fail confusingly or, worse, read one
    person's repositories on another's behalf.
    """
    import ast
    from pathlib import Path as _Path

    import archbro.platform.runtime.connector_sync as module

    forbidden = ("provider_gateway", "provider_oauth", "ExternalMcpGateway")
    tree = ast.parse(_Path(module.__file__).read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported = f"{node.module or ''} {','.join(a.name for a in node.names)}"
        elif isinstance(node, ast.Import):
            imported = ",".join(alias.name for alias in node.names)
        else:
            continue
        for pattern in forbidden:
            assert pattern not in imported, (
                f"connector_sync imports {imported!r}; deployment-scoped ingestion "
                "must not reach the per-user provider gateway"
            )
