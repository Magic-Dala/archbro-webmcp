from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archbro.backend.core.contracts import (
    AgentRunResult,
    Architecture,
    ArchitectureChangeProposal,
    ObservationClaim,
    ObservationClaimState,
    Project,
    ProjectContext,
    ProjectEvent,
    ProposalStatus,
    Task,
)
from archbro.backend.core.observation import ObservationMutationPlan, ObservationRejectedError


_OBSERVATION_CLAIM_TTL = timedelta(minutes=2)


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
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, source_key TEXT);
            CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, project_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_processing (
                event_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, note TEXT NOT NULL);
            """)
            event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "source_key" not in event_columns:
                conn.execute("ALTER TABLE events ADD COLUMN source_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_key ON events(source_key) WHERE source_key IS NOT NULL"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_event ON agent_runs(event_id)")

    @staticmethod
    def _source_key(event: ProjectEvent) -> str | None:
        if not event.source_event_id:
            return None
        return f"{event.project_id}|{event.source.value}|{event.source_event_id}"

    @staticmethod
    def _same_observation(left: ProjectEvent, right: ProjectEvent) -> bool:
        return (
            left.project_id == right.project_id
            and left.source == right.source
            and left.type == right.type
            and left.payload == right.payload
            and (
                left.source_event_id == right.source_event_id
                or left.source_event_id is None
                or right.source_event_id is None
            )
        )

    @staticmethod
    def _claim_is_stale(updated_at: str) -> bool:
        try:
            claimed_at = datetime.fromisoformat(updated_at)
        except ValueError:
            return True
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - claimed_at > _OBSERVATION_CLAIM_TTL

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
            conn.execute("DELETE FROM agent_runs WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM event_processing WHERE project_id=?", (project_id,))
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

    def save_acceptance_state(
        self,
        *,
        project_id: str,
        expected_architecture_version: int,
        expected_task_updated_at: dict[str, str],
        project: Project,
        architecture: Architecture,
        tasks: list[Task],
        proposal: ArchitectureChangeProposal,
    ) -> None:
        # Acceptance is one domain transition. Keep architecture, project version,
        # reconciled tasks, and proposal status in the same SQLite transaction so
        # a persistence failure cannot expose a half-accepted architecture.
        conn = self._connect()
        try:
            # Acquire the write reservation before re-checking the accepted base
            # state. This closes the race between ActionExecutor's planning read
            # and persistence when two approvals arrive at nearly the same time.
            conn.execute("BEGIN IMMEDIATE")
            architecture_row = conn.execute(
                "SELECT data FROM architectures WHERE project_id=?",
                (project_id,),
            ).fetchone()
            current_architecture = (
                Architecture.model_validate_json(architecture_row["data"])
                if architecture_row
                else Architecture()
            )
            if current_architecture.version != expected_architecture_version:
                raise ValueError(
                    "accepted architecture changed before proposal commit: "
                    f"expected v{expected_architecture_version}, current is v{current_architecture.version}"
                )

            project_row = conn.execute(
                "SELECT data FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if not project_row:
                raise KeyError(project_id)
            current_project = Project.model_validate_json(project_row["data"])
            if current_project.architecture_version != expected_architecture_version:
                raise ValueError(
                    "project architecture version changed before proposal commit: "
                    f"expected v{expected_architecture_version}, current is v{current_project.architecture_version}"
                )

            proposal_row = conn.execute(
                "SELECT data FROM proposals WHERE id=? AND project_id=?",
                (proposal.id, project_id),
            ).fetchone()
            if not proposal_row:
                raise KeyError(proposal.id)
            current_proposal = ArchitectureChangeProposal.model_validate_json(proposal_row["data"])
            if current_proposal.status != ProposalStatus.PENDING:
                raise ValueError("proposal is no longer pending at acceptance commit")

            for task_id, expected_updated_at in expected_task_updated_at.items():
                task_row = conn.execute(
                    "SELECT project_id, data FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
                if not task_row or task_row["project_id"] != project_id:
                    raise ValueError("acceptance task changed before proposal commit")
                current_task = Task.model_validate_json(task_row["data"])
                if current_task.updated_at.isoformat() != expected_updated_at:
                    raise ValueError("acceptance task changed before proposal commit")
            conn.execute(
                "INSERT OR REPLACE INTO architectures VALUES (?, ?)",
                (project_id, architecture.model_dump_json()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO projects VALUES (?, ?)",
                (project.id, project.model_dump_json()),
            )
            for task in tasks:
                conn.execute(
                    "INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                    (task.id, project_id, task.model_dump_json()),
                )
            conn.execute(
                "INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)",
                (proposal.id, proposal.project_id, proposal.model_dump_json()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_proposal_decision(
        self,
        *,
        project_id: str,
        proposal: ArchitectureChangeProposal,
        expected_status: ProposalStatus,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM proposals WHERE id=? AND project_id=?",
                (proposal.id, project_id),
            ).fetchone()
            if not row:
                raise KeyError(proposal.id)
            current = ArchitectureChangeProposal.model_validate_json(row["data"])
            if current.status != expected_status:
                raise ValueError(
                    "proposal status changed before decision commit: "
                    f"expected {expected_status.value}, current is {current.status.value}"
                )
            conn.execute(
                "INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)",
                (proposal.id, proposal.project_id, proposal.model_dump_json()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_event(self, event: ProjectEvent) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            source_key = self._source_key(event)
            source_row = None
            if source_key is not None:
                source_row = conn.execute(
                    "SELECT id, data, source_key FROM events WHERE source_key=?",
                    (source_key,),
                ).fetchone()
            if source_row is not None:
                existing = ProjectEvent.model_validate_json(source_row["data"])
                if not self._same_observation(existing, event):
                    raise ObservationRejectedError(
                        "source event id is already registered with different observation data"
                    )
                conn.commit()
                return

            id_row = conn.execute(
                "SELECT id, data, source_key FROM events WHERE id=?",
                (event.id,),
            ).fetchone()
            if id_row is not None:
                existing = ProjectEvent.model_validate_json(id_row["data"])
                if not self._same_observation(existing, event):
                    raise ObservationRejectedError(
                        "event id is already registered with different observation data"
                    )
                existing_source_key = id_row["source_key"]
                if existing_source_key is not None and source_key is not None and existing_source_key != source_key:
                    raise ObservationRejectedError(
                        "event id is already registered with a different source event id"
                    )
                if existing.source_event_id is None and event.source_event_id is not None:
                    existing = existing.model_copy(update={"source_event_id": event.source_event_id})
                    conn.execute(
                        "UPDATE events SET data=?, source_key=? WHERE id=?",
                        (existing.model_dump_json(), source_key, existing.id),
                    )
                elif existing_source_key is None and source_key is not None:
                    conn.execute(
                        "UPDATE events SET source_key=? WHERE id=?",
                        (source_key, existing.id),
                    )
                conn.commit()
                return

            conn.execute(
                "INSERT INTO events(id, project_id, data, source_key) VALUES (?, ?, ?, ?)",
                (event.id, event.project_id, event.model_dump_json(), source_key),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_event(self, event_id: str) -> ProjectEvent:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        return ProjectEvent.model_validate_json(row["data"])

    def list_events(self, project_id: str, limit: int = 100) -> list[ProjectEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM events WHERE project_id=? ORDER BY rowid DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        events = [ProjectEvent.model_validate_json(row["data"]) for row in rows]
        return list(reversed(events))

    def list_agent_runs(self, project_id: str, limit: int = 100) -> list[AgentRunResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM agent_runs WHERE project_id=? ORDER BY rowid DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        runs = [AgentRunResult.model_validate_json(row["data"]) for row in rows]
        return list(reversed(runs))

    def claim_observation(self, event: ProjectEvent, *, run_id: str) -> ObservationClaim:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (event.project_id,)).fetchone():
                raise KeyError(event.project_id)

            source_key = self._source_key(event)
            row = None
            if source_key is not None:
                row = conn.execute(
                    "SELECT data FROM events WHERE source_key=?",
                    (source_key,),
                ).fetchone()
            if row is None:
                row = conn.execute("SELECT data FROM events WHERE id=?", (event.id,)).fetchone()

            if row is not None:
                canonical_event = ProjectEvent.model_validate_json(row["data"])
                if not self._same_observation(canonical_event, event):
                    raise ObservationRejectedError(
                        "source event id is already registered with different observation data"
                    )
                if source_key is not None:
                    if canonical_event.source_event_id is None and event.source_event_id is not None:
                        canonical_event = canonical_event.model_copy(
                            update={"source_event_id": event.source_event_id}
                        )
                        conn.execute(
                            "UPDATE events SET data=? WHERE id=?",
                            (canonical_event.model_dump_json(), canonical_event.id),
                        )
                    conn.execute(
                        "UPDATE events SET source_key=COALESCE(source_key, ?) WHERE id=?",
                        (source_key, canonical_event.id),
                    )
            else:
                canonical_event = event
                conn.execute(
                    "INSERT INTO events(id, project_id, data, source_key) VALUES (?, ?, ?, ?)",
                    (
                        canonical_event.id,
                        canonical_event.project_id,
                        canonical_event.model_dump_json(),
                        source_key,
                    ),
                )

            processing = conn.execute(
                "SELECT run_id, state, updated_at FROM event_processing WHERE event_id=?",
                (canonical_event.id,),
            ).fetchone()
            if processing is not None and processing["state"] == "SUCCESS":
                run_row = conn.execute(
                    "SELECT data FROM agent_runs WHERE id=?",
                    (processing["run_id"],),
                ).fetchone()
                if not run_row:
                    raise RuntimeError("completed observation is missing its AgentRun")
                result = AgentRunResult.model_validate_json(run_row["data"])
                conn.commit()
                return ObservationClaim(
                    state=ObservationClaimState.REPLAY,
                    event=canonical_event,
                    run_id=result.agent_run_id,
                    existing_result=result,
                )

            if (
                processing is not None
                and processing["state"] == "PROCESSING"
                and not self._claim_is_stale(processing["updated_at"])
            ):
                conn.commit()
                return ObservationClaim(
                    state=ObservationClaimState.IN_PROGRESS,
                    event=canonical_event,
                    run_id=processing["run_id"],
                )

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO event_processing(event_id, project_id, run_id, state, updated_at)
                VALUES (?, ?, ?, 'PROCESSING', ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    state='PROCESSING',
                    updated_at=excluded.updated_at
                """,
                (canonical_event.id, canonical_event.project_id, run_id, now),
            )
            conn.commit()
            return ObservationClaim(
                state=ObservationClaimState.CLAIMED,
                event=canonical_event,
                run_id=run_id,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _assert_processing_claim(conn: sqlite3.Connection, event_id: str, run_id: str) -> None:
        row = conn.execute(
            "SELECT run_id, state FROM event_processing WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if not row or row["state"] != "PROCESSING" or row["run_id"] != run_id:
            raise ValueError("observation processing claim changed before commit")

    def commit_observation_result(
        self,
        *,
        event: ProjectEvent,
        run_id: str,
        plan: ObservationMutationPlan,
        result: AgentRunResult,
    ) -> None:
        if result.result != "SUCCESS" or result.agent_run_id != run_id or result.event_id != event.id:
            raise ValueError("successful observation commit requires the claimed successful AgentRun")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_processing_claim(conn, event.id, run_id)

            if plan.expected_project_updated_at is not None:
                project_row = conn.execute(
                    "SELECT data FROM projects WHERE id=?",
                    (event.project_id,),
                ).fetchone()
                if not project_row:
                    raise KeyError(event.project_id)
                current_project = Project.model_validate_json(project_row["data"])
                if current_project.updated_at.isoformat() != plan.expected_project_updated_at:
                    raise ValueError("observation project state changed before commit")

            if plan.expected_architecture_version is not None:
                architecture_row = conn.execute(
                    "SELECT data FROM architectures WHERE project_id=?",
                    (event.project_id,),
                ).fetchone()
                current_architecture = (
                    Architecture.model_validate_json(architecture_row["data"])
                    if architecture_row
                    else Architecture()
                )
                if current_architecture.version != plan.expected_architecture_version:
                    raise ValueError("observation architecture state changed before commit")

            for task_id, expected_updated_at in plan.expected_task_updated_at.items():
                task_row = conn.execute(
                    "SELECT project_id, data FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
                if not task_row or task_row["project_id"] != event.project_id:
                    raise ValueError("observation task state changed before commit")
                current_task = Task.model_validate_json(task_row["data"])
                if current_task.updated_at.isoformat() != expected_updated_at:
                    raise ValueError("observation task state changed before commit")

            for proposal in plan.proposals:
                if proposal.project_id != event.project_id:
                    raise ValueError("proposal project_id mismatch during observation commit")
                for evidence_event_id in proposal.evidence_event_ids:
                    evidence_row = conn.execute(
                        "SELECT project_id FROM events WHERE id=?",
                        (evidence_event_id,),
                    ).fetchone()
                    if not evidence_row or evidence_row["project_id"] != event.project_id:
                        raise ValueError("proposal evidence must reference an event from the same project")

            if plan.architecture is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO architectures(project_id, data) VALUES (?, ?)",
                    (event.project_id, plan.architecture.model_dump_json()),
                )
            if plan.project is not None:
                if plan.project.id != event.project_id:
                    raise ValueError("project mutation does not match observation project")
                conn.execute(
                    "INSERT OR REPLACE INTO projects(id, data) VALUES (?, ?)",
                    (plan.project.id, plan.project.model_dump_json()),
                )
            for task in plan.tasks:
                conn.execute(
                    "INSERT OR REPLACE INTO tasks(id, project_id, data) VALUES (?, ?, ?)",
                    (task.id, event.project_id, task.model_dump_json()),
                )
            for proposal in plan.proposals:
                conn.execute(
                    "INSERT OR REPLACE INTO proposals(id, project_id, data) VALUES (?, ?, ?)",
                    (proposal.id, proposal.project_id, proposal.model_dump_json()),
                )
            for note in plan.notes:
                conn.execute(
                    "INSERT INTO notes(project_id, note) VALUES (?, ?)",
                    (event.project_id, note),
                )

            conn.execute(
                "INSERT INTO agent_runs(id, event_id, project_id, data) VALUES (?, ?, ?, ?)",
                (result.agent_run_id, event.id, event.project_id, result.model_dump_json()),
            )
            conn.execute(
                "UPDATE event_processing SET state='SUCCESS', updated_at=? WHERE event_id=? AND run_id=?",
                (datetime.now(timezone.utc).isoformat(), event.id, run_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_observation(
        self,
        *,
        event: ProjectEvent,
        run_id: str,
        result: AgentRunResult,
    ) -> None:
        if result.result != "ERROR" or result.agent_run_id != run_id or result.event_id != event.id:
            raise ValueError("failed observation commit requires the claimed failed AgentRun")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_processing_claim(conn, event.id, run_id)
            conn.execute(
                "INSERT INTO agent_runs(id, event_id, project_id, data) VALUES (?, ?, ?, ?)",
                (result.agent_run_id, event.id, event.project_id, result.model_dump_json()),
            )
            conn.execute(
                "UPDATE event_processing SET state='FAILED', updated_at=? WHERE event_id=? AND run_id=?",
                (datetime.now(timezone.utc).isoformat(), event.id, run_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def commit_event_actions(
        self,
        *,
        event: ProjectEvent,
        project: Project | None,
        architecture: Architecture | None,
        tasks: list[Task],
        proposals: list[ArchitectureChangeProposal],
        notes: list[str],
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (event.project_id,)).fetchone():
                raise KeyError(event.project_id)
            if project is not None and project.id != event.project_id:
                raise ValueError("project mutation does not match event project")
            for task in tasks:
                row = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task.id,)).fetchone()
                if row and row["project_id"] != event.project_id:
                    raise ValueError("task mutation does not match event project")
            for proposal in proposals:
                if proposal.project_id != event.project_id:
                    raise ValueError("proposal project_id mismatch during event commit")

            conn.execute(
                "INSERT OR REPLACE INTO events VALUES (?, ?, ?)",
                (event.id, event.project_id, event.model_dump_json()),
            )
            if architecture is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO architectures VALUES (?, ?)",
                    (event.project_id, architecture.model_dump_json()),
                )
            if project is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO projects VALUES (?, ?)",
                    (project.id, project.model_dump_json()),
                )
            for task in tasks:
                conn.execute(
                    "INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                    (task.id, event.project_id, task.model_dump_json()),
                )
            for proposal in proposals:
                conn.execute(
                    "INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)",
                    (proposal.id, proposal.project_id, proposal.model_dump_json()),
                )
            for note in notes:
                conn.execute(
                    "INSERT INTO notes(project_id, note) VALUES (?, ?)",
                    (event.project_id, note),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
