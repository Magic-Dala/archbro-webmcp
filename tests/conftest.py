"""Shared PostgreSQL fixtures.

PostgreSQL is Archbro's only persistence backend, so every test that needs a
repository runs against a real database -- there is no fake. Each test gets its
own schema, created before the test and dropped afterwards, so the tests are
isolated from each other and repeatable without a manual database reset.

Modules that need a database set ``pytestmark = requires_database`` so a missing
DATABASE_URL skips them instead of erroring. The test harness reads only
DATABASE_URL from the repository .env when the process environment does not
already provide one; it deliberately does not load the rest of .env, so a
normal pytest run cannot accidentally enable real-provider tests such as Gemini.
"""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from archbro.platform.persistence.postgres import PostgresProjectRepository


def _configured_database_url() -> str:
    configured = (os.getenv("DATABASE_URL") or "").strip()
    if configured:
        return configured
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return ""
    return str(dotenv_values(env_path).get("DATABASE_URL") or "").strip()


DATABASE_URL = _configured_database_url()

requires_database = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _schema_dsn(schema: str) -> str:
    """Return a schema-scoped DSN for either URI or libpq key/value conninfo."""
    return make_conninfo(DATABASE_URL, options=f"-c search_path={schema}")


@pytest.fixture
def dsn():
    """Give every test a private, empty schema and drop it afterwards."""
    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _schema_dsn(schema)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def repo(dsn):
    return PostgresProjectRepository(dsn)
