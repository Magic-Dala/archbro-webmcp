"""Durable sync position for pull-based external sources.

A webhook delivery carries its own identity. A pull-based source does not, so the
pipeline must remember where it stopped or every sync re-reads the whole history.
The position itself is opaque: only the provider adapter knows whether it is a
timestamp, a message id, or a change token.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from archbro.platform.pipeline.contracts import SyncCursor
from archbro.platform.pipeline.cursor import SqliteSyncCursorStore


def _store() -> tuple[SqliteSyncCursorStore, str]:
    path = str(Path(tempfile.mkdtemp()) / "cursors.db")
    return SqliteSyncCursorStore(path), path


def test_unknown_connector_has_no_position_yet():
    store, _ = _store()

    assert store.load("proj_1", "github") is None


def test_saved_position_is_returned_for_the_same_connector():
    store, _ = _store()

    store.save(
        SyncCursor(
            project_id="proj_1",
            connector_id="github",
            position="2026-08-28T12:00:00+00:00",
            owner_user_id="firebase-uid-alice",
        )
    )

    loaded = store.load("proj_1", "github")
    assert loaded is not None
    assert loaded.position == "2026-08-28T12:00:00+00:00"
    assert loaded.owner_user_id == "firebase-uid-alice"
    assert loaded.updated_at is not None


def test_advancing_the_position_updates_in_place():
    store, _ = _store()
    cursor = SyncCursor(project_id="proj_1", connector_id="github", position="first")

    store.save(cursor)
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="second"))

    loaded = store.load("proj_1", "github")
    assert loaded is not None
    assert loaded.position == "second"
    assert len(store.list_cursors("proj_1")) == 1


def test_positions_are_isolated_per_project_and_connector():
    store, _ = _store()

    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="a"))
    store.save(SyncCursor(project_id="proj_1", connector_id="slack", position="b"))
    store.save(SyncCursor(project_id="proj_2", connector_id="github", position="c"))

    assert store.load("proj_1", "github").position == "a"
    assert store.load("proj_1", "slack").position == "b"
    assert store.load("proj_2", "github").position == "c"
    assert len(store.list_cursors("proj_1")) == 2


def test_position_survives_reopening_the_database():
    store, path = _store()
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="kept"))

    reopened = SqliteSyncCursorStore(path)

    loaded = reopened.load("proj_1", "github")
    assert loaded is not None
    assert loaded.position == "kept"


def test_a_stale_writer_cannot_move_the_position_backwards():
    """Two workers can read the same starting position concurrently.

    Whoever commits second must not silently overwrite a newer position with an
    older one; with opaque page or change tokens the store cannot detect that a
    regression happened, so the write has to be conditional.
    """
    store, _ = _store()
    store.save(SyncCursor(project_id="proj_1", connector_id="github", position="start"))

    # Worker B advances first.
    assert store.advance("proj_1", "github", expected_position="start", position="20") is True
    # Worker A resumes with the stale read and must lose the race.
    assert store.advance("proj_1", "github", expected_position="start", position="10") is False

    assert store.load("proj_1", "github").position == "20"


def test_first_advance_requires_no_existing_position():
    store, _ = _store()

    assert store.advance("proj_1", "github", expected_position=None, position="first") is True
    assert store.advance("proj_1", "github", expected_position=None, position="again") is False
    assert store.load("proj_1", "github").position == "first"


def test_connector_identity_is_required():
    with pytest.raises(ValueError, match="project_id"):
        SyncCursor(project_id="  ", connector_id="github")

    with pytest.raises(ValueError, match="connector_id"):
        SyncCursor(project_id="proj_1", connector_id="")
