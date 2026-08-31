"""Copy an ArchBro SQLite database into PostgreSQL.

Both stores keep the same document shape -- an id column plus a `data` column
holding the serialised model -- so rows move across verbatim rather than being
re-serialised. Nothing is reinterpreted, so a row that was readable before is
readable after.

Row counts are compared **before** committing. A migration that would not
reproduce the source exactly is rolled back rather than reported after the
fact, because a lossy migration you have already committed is not a warning,
it is data loss.

    docker compose run --rm app python qa/migrate_sqlite_to_postgres.py --sqlite archbro.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import psycopg

from archbro.platform.persistence.postgres import PostgresProjectRepository

# Tables keyed by a natural id, so re-running updates in place.
KEYED_TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    "projects": (("id", "data"), "id"),
    "architectures": (("project_id", "data"), "project_id"),
    "tasks": (("id", "project_id", "data"), "id"),
    "proposals": (("id", "project_id", "data"), "id"),
    "events": (("id", "project_id", "data", "source_key"), "id"),
    "agent_runs": (("id", "event_id", "project_id", "data"), "id"),
    "event_processing": (("event_id", "project_id", "run_id", "state", "updated_at"), "event_id"),
}
ALL_TABLES = list(KEYED_TABLES) + ["notes"]


def count(cursor, table: str) -> int:
    return cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="archbro.db")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument(
        "--replace-notes",
        action="store_true",
        help="delete existing notes first; required when the target already has any",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("DATABASE_URL is not set and --database-url was not given", file=sys.stderr)
        return 2
    if not os.path.exists(args.sqlite):
        print(f"{args.sqlite} does not exist", file=sys.stderr)
        return 2

    # Constructing the repository creates the schema if it is absent.
    PostgresProjectRepository(args.database_url)

    source = sqlite3.connect(args.sqlite)
    source.row_factory = sqlite3.Row

    # autocommit=False: everything below is one transaction, committed only
    # after the row counts match.
    with psycopg.connect(args.database_url) as target:
        with target.cursor() as cursor:
            # Preflight before any write. psycopg commits when its connection
            # context exits normally, so refusing after writing the keyed
            # tables would persist exactly the rows the refusal claims not to
            # have migrated. Check first, and roll back explicitly so a future
            # early return cannot reintroduce that.
            existing_notes = count(cursor, "notes")
            if existing_notes and not args.replace_notes:
                target.rollback()
                print(
                    f"target already holds {existing_notes} notes. Notes cannot be matched to "
                    "the source because duplicates are legal, so re-running would either "
                    "duplicate or undercount them. Re-run with --replace-notes to replace "
                    "them wholesale.",
                    file=sys.stderr,
                )
                return 1

            for table, (columns, conflict_key) in KEYED_TABLES.items():
                rows = source.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
                if not rows:
                    continue
                placeholders = ", ".join(["%s"] * len(columns))
                updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in columns if c != conflict_key)
                statement = (
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({conflict_key}) DO UPDATE SET {updates}"
                )
                for row in rows:
                    cursor.execute(statement, tuple(row[c] for c in columns))

            # notes has no natural key and duplicate (project_id, note) rows are
            # legitimate history in both schemas, so they are inserted as-is.
            # Deduplicating them would silently collapse real history, and
            # skipping existing ones would make a re-run undercount.
            if args.replace_notes:
                cursor.execute("DELETE FROM notes")
            for row in source.execute("SELECT project_id, note FROM notes").fetchall():
                cursor.execute(
                    "INSERT INTO notes (project_id, note) VALUES (%s, %s)",
                    (row["project_id"], row["note"]),
                )

            print(f"{'table':<20} {'sqlite':>8} {'postgres':>10}")
            mismatched = []
            for table in ALL_TABLES:
                src = count(source, table)
                dst = count(cursor, table)
                marker = "" if src == dst else "   MISMATCH"
                if src != dst:
                    mismatched.append(table)
                print(f"{table:<20} {src:>8} {dst:>10}{marker}")

            if mismatched:
                target.rollback()
                print(
                    f"\nrolled back: {', '.join(mismatched)} would not match the source",
                    file=sys.stderr,
                )
                return 1

        target.commit()

    source.close()
    print("\nmigration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
