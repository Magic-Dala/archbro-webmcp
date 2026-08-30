from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from archbro.platform.pipeline.contracts import SyncCursor


class SqliteSyncCursorStore:
    """Local durable sync positions.

    Kept in its own table rather than inside the project repository: a cursor is
    delivery bookkeeping, not canonical project state, and must never be mistaken
    for evidence the Agent can reason about.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_cursors (
                    project_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    position TEXT,
                    owner_user_id TEXT,
                    stalled_attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, connector_id)
                )
                """
            )

    @staticmethod
    def _row_to_cursor(row: sqlite3.Row) -> SyncCursor:
        updated_at = row["updated_at"]
        return SyncCursor(
            project_id=row["project_id"],
            connector_id=row["connector_id"],
            position=row["position"],
            owner_user_id=row["owner_user_id"],
            stalled_attempts=row["stalled_attempts"],
            updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
        )

    def load(self, project_id: str, connector_id: str) -> SyncCursor | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_cursors WHERE project_id=? AND connector_id=?",
                (project_id, connector_id),
            ).fetchone()
        return self._row_to_cursor(row) if row is not None else None

    def save(self, cursor: SyncCursor) -> None:
        updated_at = (cursor.updated_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_cursors(
                    project_id, connector_id, position, owner_user_id, stalled_attempts, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, connector_id) DO UPDATE SET
                    position=excluded.position,
                    owner_user_id=excluded.owner_user_id,
                    stalled_attempts=excluded.stalled_attempts,
                    updated_at=excluded.updated_at
                """,
                (
                    cursor.project_id,
                    cursor.connector_id,
                    cursor.position,
                    cursor.owner_user_id,
                    cursor.stalled_attempts,
                    updated_at,
                ),
            )

    def advance(
        self,
        project_id: str,
        connector_id: str,
        *,
        expected_position: str | None,
        position: str,
        owner_user_id: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if expected_position is None:
                # A first advance may create the row, but must not overwrite a
                # position another worker already established.
                cursor = conn.execute(
                    """
                    INSERT INTO sync_cursors(
                        project_id, connector_id, position, owner_user_id, stalled_attempts, updated_at
                    )
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(project_id, connector_id) DO UPDATE SET
                        position=excluded.position,
                        owner_user_id=excluded.owner_user_id,
                        stalled_attempts=0,
                        updated_at=excluded.updated_at
                    WHERE sync_cursors.position IS NULL
                    """,
                    (project_id, connector_id, position, owner_user_id, now),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE sync_cursors
                       SET position=?,
                           owner_user_id=COALESCE(?, owner_user_id),
                           stalled_attempts=0,
                           updated_at=?
                     WHERE project_id=? AND connector_id=? AND position=?
                    """,
                    (position, owner_user_id, now, project_id, connector_id, expected_position),
                )
            return cursor.rowcount > 0

    def record_stall(self, project_id: str, connector_id: str, attempts: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_cursors(
                    project_id, connector_id, position, owner_user_id, stalled_attempts, updated_at
                )
                VALUES (?, ?, NULL, NULL, ?, ?)
                ON CONFLICT(project_id, connector_id) DO UPDATE SET
                    stalled_attempts=excluded.stalled_attempts,
                    updated_at=excluded.updated_at
                """,
                (project_id, connector_id, attempts, now),
            )

    def list_cursors(self, project_id: str) -> list[SyncCursor]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_cursors WHERE project_id=? ORDER BY connector_id",
                (project_id,),
            ).fetchall()
        return [self._row_to_cursor(row) for row in rows]
