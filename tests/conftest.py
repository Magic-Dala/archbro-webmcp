"""Shared PostgreSQL fixtures.

PostgreSQL is Archbro's only persistence backend, so every test that needs a
repository runs against a real database -- there is no fake. Each test gets its
own schema, created before the test and dropped afterwards, so the tests are
isolated from each other and repeatable without a manual database reset.

Modules that need a database set `pytestmark = requires_database` so a missing
DATABASE_URL skips them instead of erroring, following the same convention
tests/test_real_gemini.py uses for a missing API key.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from archbro.platform.persistence.postgres import PostgresProjectRepository

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

requires_database = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _schema_dsn(schema: str) -> str:
    separator = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{separator}options=-csearch_path%3D{schema}"


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
