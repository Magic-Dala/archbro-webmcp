from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from archbro.backend.core.contracts import Architecture, ArchitectureChangeProposal, Project, ProjectContext, ProjectEvent, ProposalStatus, Task


class ProjectRepository:
    def __init__(self, db_path: str = "archbro.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS architectures (project_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, note TEXT NOT NULL);
            """)

    def save_project(self, project: Project) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO projects VALUES (?, ?)", (project.id, project.model_dump_json()))

    def get_project(self, project_id: str) -> Project:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        return Project.model_validate_json(row["data"])

    def list_projects(self) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY rowid DESC").fetchall()
        return [Project.model_validate_json(row["data"]) for row in rows]

    def delete_project(self, project_id: str) -> bool:
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM architectures WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM proposals WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM events WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM notes WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return True

    def save_architecture(self, project_id: str, architecture: Architecture) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO architectures VALUES (?, ?)", (project_id, architecture.model_dump_json()))

    def get_architecture(self, project_id: str) -> Architecture:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM architectures WHERE project_id=?", (project_id,)).fetchone()
        return Architecture.model_validate_json(row["data"]) if row else Architecture()

    def save_task(self, project_id: str, task: Task) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)", (task.id, project_id, task.model_dump_json()))

    def get_task(self, task_id: str) -> Task:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        return Task.model_validate_json(row["data"])

    def list_tasks(self, project_id: str) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM tasks WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return [Task.model_validate_json(r["data"]) for r in rows]

    def save_proposal(self, proposal: ArchitectureChangeProposal) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)", (proposal.id, proposal.project_id, proposal.model_dump_json()))

    def get_proposal(self, proposal_id: str) -> ArchitectureChangeProposal:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return ArchitectureChangeProposal.model_validate_json(row["data"])

    def list_proposals(self, project_id: str) -> list[ArchitectureChangeProposal]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM proposals WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return [ArchitectureChangeProposal.model_validate_json(r["data"]) for r in rows]

    def save_event(self, event: ProjectEvent) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO events VALUES (?, ?, ?)", (event.id, event.project_id, event.model_dump_json()))

    def add_note(self, project_id: str, note: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO notes(project_id, note) VALUES (?, ?)", (project_id, note))

    def list_notes(self, project_id: str, limit: int = 20) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT note FROM notes WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit)).fetchall()
        return [r["note"] for r in reversed(rows)]

    def load_context(self, project_id: str) -> ProjectContext:
        return ProjectContext(
            project=self.get_project(project_id),
            architecture=self.get_architecture(project_id),
            tasks=self.list_tasks(project_id),
            pending_proposals=[p for p in self.list_proposals(project_id) if p.status == ProposalStatus.PENDING],
            recent_notes=self.list_notes(project_id),
        )

    def snapshot(self, project_id: str) -> str:
        return json.dumps(self.load_context(project_id).model_dump(mode="json"), sort_keys=True)
