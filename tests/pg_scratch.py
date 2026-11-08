"""Scratch-schema helper for the two database test files.

The database tests must never create or drop databases, and must never touch any schema they did not create.
They build their tables in a temporary schema named `clockyn_dev_test_<8 hex>` inside an EXISTING database and drop
only that schema afterwards. `drop_scratch_schema` refuses any other name, so a bug cannot reach a real schema.

Connection settings, first match wins: TEST_PG_* environment variables, TEST_PG_* lines of the backend `.env`, DB_* lines
of the backend `.env`. The database is TEST_PG_DBNAME, else DB_NAME. PGSSLMODE (environment) is honoured by libpq.
"""
import os
import re
import time
import uuid
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "clockyn_dev_test_"
_NAME_RE = re.compile(r"^clockyn_dev_test_[0-9a-f]{8}$")
BASELINE_SCHEMA = "clockyn_dev"  # the qualifier used in clockyn_ddl_latest.sql


def _env_file() -> dict:
    out = {}
    try:
        for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def settings() -> dict:
    """Connection keyword arguments, or skip the whole module when nothing usable is configured."""
    file = _env_file()

    def pick(*names):
        for name in names:
            value = os.environ.get(name) or file.get(name)
            if value and "<" not in value:
                return value
        return None

    host = pick("TEST_PG_HOST", "DB_TEST_HOST", "DB_HOST")
    dbname = pick("TEST_PG_DBNAME", "DB_NAME")
    user = pick("TEST_PG_USER", "DB_TEST_USER", "DB_USER")
    password = pick("TEST_PG_PASSWORD", "DB_TEST_PASSWORD", "DB_PASSWORD")
    port = pick("TEST_PG_PORT", "DB_TEST_PORT", "DB_PORT") or "5432"
    if not (host and dbname and user and password):
        pytest.skip("no test database configured (TEST_PG_* or DB_* in .env)", allow_module_level=True)
    return dict(host=host, port=int(port), dbname=dbname, user=user, password=password)


def connect(retries: int = 3):
    cfg = settings()
    last = None
    for _ in range(retries):
        try:
            return psycopg2.connect(connect_timeout=30, **cfg)
        except psycopg2.OperationalError as exc:  # pragma: no cover - network dependent
            last = exc
            time.sleep(2)
    raise last


def create_scratch_schema(conn) -> str:
    name = PREFIX + uuid.uuid4().hex[:8]
    assert _NAME_RE.match(name)
    conn.autocommit = True
    conn.cursor().execute(f'CREATE SCHEMA "{name}"')
    return name


def drop_scratch_schema(conn, name: str) -> None:
    """Drop a scratch schema created by this module. Any other name is refused."""
    if not _NAME_RE.match(name or ""):
        raise RuntimeError(f"refusing to drop schema {name!r}: not a scratch schema")
    conn.autocommit = True
    conn.cursor().execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def rewrite_ddl(text: str, schema: str) -> str:
    """Point the baseline DDL (qualified with clockyn_dev) at the scratch schema."""
    assert _NAME_RE.match(schema)
    return text.replace(BASELINE_SCHEMA, schema)


def app_env(schema: str) -> dict:
    """Environment the application code needs to talk to the scratch schema."""
    cfg = settings()
    return dict(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]), DB_USER=cfg["user"], DB_PASSWORD=cfg["password"],
                DB_NAME=cfg["dbname"], DB_SCHEMA=schema)
