"""Live SQL check: every SQL statement of the backend is validated by PostgreSQL's own parser/planner.

Skipped automatically when no database is configured (pg_scratch.settings() skips the module).
Safety rules (see scripts/sql_live_check.py): connection only through tests/pg_scratch.py; everything happens in a
scratch schema `clockyn_dev_test_<hex>` that is created and dropped here; search_path is that schema only; nothing from
the code is executed (only `PREPARE ... AS <statement>` on a read-only session); migrations run in a rolled-back
transaction inside the scratch schema.

Run: PGSSLMODE=require python -m pytest tests/test_sql_live.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

import pytest
import pg_scratch

pg_scratch.settings()  # skips this whole module when no test database is configured

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import sql_live_check as L  # noqa: E402


@pytest.fixture(scope="module")
def report():
    rep = L.run_live_check()
    rep.pop("_results", None)
    return rep


def _fmt(rows):
    return "\n".join(f"{r['file']}:{r['line']} {r['code']} {r['error']} | {r['snippet'][:120]}" for r in rows)


def test_no_real_sql_errors(report):
    assert report["real_errors"] == [], "PostgreSQL rejects these statements:\n" + _fmt(report["real_errors"])


def test_migrations_apply_cleanly_in_a_rolled_back_transaction(report):
    bad = [m for m in report["migrations"] if not m["ok"]]
    assert bad == [], bad
    assert len(report["migrations"]) >= 10


def test_the_whole_backend_was_covered(report):
    assert report["statements_checked"] > 700
    assert sum(report["counts"].values()) == report["statements_checked"]
    assert report["counts"].get("OK", 0) > 600


def test_untyped_noise_is_baselined(report):
    """A new statement whose parameter types cannot be inferred is harmless (psycopg2 sends literals) but must be
    acknowledged: add its key to scripts/sql_live_baseline.json (python scripts/sql_live_check.py --write-baseline)."""
    assert report["untyped_not_in_baseline"] == [], _fmt(report["untyped_not_in_baseline"])


def test_no_scratch_schema_is_left_behind(report):
    conn = pg_scratch.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'clockyn\\_dev\\_test\\_%'")
        # other test modules clean up after themselves; this module's own schema was dropped in run_live_check
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------
# negative controls: the validator must really reject wrong SQL (its own tiny scratch schema, dropped afterwards)
# ---------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def validator():
    admin = pg_scratch.connect()
    schema = pg_scratch.create_scratch_schema(admin)
    vconn = None
    try:
        admin.cursor().execute(f'SET search_path TO "{schema}"')
        admin.cursor().execute("CREATE TABLE t (id int, a int, b text, d date)")
        vconn = pg_scratch.connect()
        yield L.Validator(vconn, schema)
    finally:
        if vconn is not None:
            vconn.close()
        pg_scratch.drop_scratch_schema(admin, schema)
        admin.close()


def _classify(validator, sql):
    return validator.prepare_text(L.to_pg(sql)[0])[0]


@pytest.mark.parametrize("sql,expected", [
    ("SELECT id, b FROM t WHERE a = %s AND d >= %s", "OK"),
    ("SELECT %s IS NULL", "UNTYPED"),
    ("SELECT nosuchcol FROM t", "REAL"),
    ("SELECT id FROM nosuchtable", "REAL"),
    ("UPDATE t SET nosuchcol = %s WHERE id = %s", "REAL"),
    ("SELECT id, count(*) FROM t GROUP BY b", "REAL"),
    ("SELECT a FROM t WHERE b = 1", "REAL"),
    ("SELECT CASE WHEN a = %s THEN 1 ELSE 2 END AS k, count(*) FROM t "
     "GROUP BY CASE WHEN a = %s THEN 1 ELSE 2 END", "OK"),
    ("INSERT INTO t (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", "REAL"),  # no unique index on id
    ("SELECT 1 FROM t WHERE id IN %s", "OK"),
    ("SET search_path TO x", "UTILITY"),
])
def test_validator_classification(validator, sql, expected):
    assert _classify(validator, sql) == expected


def test_validator_never_leaves_the_scratch_schema(validator):
    cur = validator.cur
    cur.execute("SELECT current_schemas(false), current_setting('default_transaction_read_only')")
    schemas, ro = cur.fetchone()
    assert list(schemas) == [validator.schema] and ro == "on"
    # a write is refused by the read-only session even if it were ever sent
    with pytest.raises(Exception):
        cur.execute("INSERT INTO t (id) VALUES (1)")
