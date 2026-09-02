"""The SQLite-to-PostgreSQL migration must reproduce the source exactly.

A migration that quietly changes what it copies is worse than one that fails:
the failure is noticed, the alteration is not.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import sys

import psycopg

from conftest import requires_database

pytestmark = requires_database

SCRIPT = Path(__file__).resolve().parents[1] / "qa" / "migrate_sqlite_to_postgres.py"


def test_direct_cli_bootstraps_the_src_layout() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'SRC_ROOT = Path(__file__).resolve().parents[1] / "src"' in source
    assert "sys.path.insert(0, str(SRC_ROOT))" in source


def _sqlite_with(path: Path, notes: list[tuple[str, str]], projects: list[tuple[str, str]] | None = None) -> Path:
    _sqlite_with_notes(path, notes)
    if projects:
        connection = sqlite3.connect(path)
        connection.executemany(
            "INSERT OR REPLACE INTO projects (id, data) VALUES (?, ?)", projects
        )
        connection.commit()
        connection.close()
    return path


def _sqlite_with_notes(path: Path, notes: list[tuple[str, str]]) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE architectures (project_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE proposals (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE events (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, source_key TEXT);
        CREATE TABLE agent_runs (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, project_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE event_processing (event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, note TEXT NOT NULL);
        """
    )
    connection.executemany("INSERT INTO notes (project_id, note) VALUES (?, ?)", notes)
    connection.commit()
    connection.close()
    return path


def _migrate(sqlite_path: Path, dsn: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--sqlite", str(sqlite_path), "--database-url", dsn, *extra],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )


def test_duplicate_notes_survive_the_migration(tmp_path, dsn):
    """Duplicate (project_id, note) rows are legal history in both schemas.

    Matching notes on their content to make the migration idempotent would
    collapse them, losing history that the source legitimately holds.
    """

    source = _sqlite_with_notes(
        tmp_path / "dup.db",
        [("p1", "same note"), ("p1", "same note"), ("p1", "same note"), ("p2", "other")],
    )

    result = _migrate(source, dsn)

    assert result.returncode == 0, result.stdout + result.stderr
    with psycopg.connect(dsn) as connection:
        total = connection.execute("SELECT count(*) FROM notes").fetchone()[0]
        repeated = connection.execute(
            "SELECT count(*) FROM notes WHERE project_id='p1' AND note='same note'"
        ).fetchone()[0]
    assert total == 4
    assert repeated == 3


def test_rerunning_refuses_rather_than_duplicating_notes(tmp_path, dsn):
    source = _sqlite_with_notes(tmp_path / "again.db", [("p1", "note")])

    assert _migrate(source, dsn).returncode == 0
    second = _migrate(source, dsn)

    assert second.returncode == 1
    assert "--replace-notes" in second.stderr
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_replace_notes_makes_a_rerun_reproduce_the_source(tmp_path, dsn):
    source = _sqlite_with_notes(tmp_path / "replace.db", [("p1", "a"), ("p1", "a")])

    assert _migrate(source, dsn).returncode == 0
    assert _migrate(source, dsn, "--replace-notes").returncode == 0

    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM notes").fetchone()[0] == 2


def test_a_refused_rerun_commits_nothing(tmp_path, dsn):
    """Refusing to run must leave the target untouched, not half-written.

    The keyed tables are written before the notes preflight can reject the run,
    and psycopg commits when its connection context exits normally -- so an
    early return would persist exactly the rows the script claims it refused
    to migrate.
    """

    first = _sqlite_with(tmp_path / "first.db", [("p1", "note")], [("proj-1", '{"name": "original"}')])
    assert _migrate(first, dsn).returncode == 0

    with psycopg.connect(dsn) as connection:
        before = connection.execute("SELECT data FROM projects WHERE id='proj-1'").fetchone()[0]
    assert "original" in before

    # Same notes (so the rerun is refused) but changed project data.
    second = _sqlite_with(tmp_path / "second.db", [("p1", "note")], [("proj-1", '{"name": "altered"}')])
    result = _migrate(second, dsn)

    assert result.returncode == 1, result.stdout + result.stderr
    with psycopg.connect(dsn) as connection:
        after = connection.execute("SELECT data FROM projects WHERE id='proj-1'").fetchone()[0]
    assert after == before, "a refused migration committed keyed-table writes anyway"
