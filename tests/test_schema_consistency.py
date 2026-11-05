"""DB-free tests for scripts/check_schema_consistency.py (DDL file vs numbered migrations).
Run: python -m pytest tests/test_schema_consistency.py -q
"""
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_schema_consistency as C  # noqa: E402

DDL = """
CREATE SCHEMA punchapp_prod;
CREATE TYPE punchapp_prod.kind_enum AS ENUM ('a', 'b');
CREATE TABLE punchapp_prod.things (
	id int4 GENERATED ALWAYS AS IDENTITY NOT NULL,
	org_id varchar(36) NOT NULL,
	name varchar(64) DEFAULT 'x'::character varying NULL,
	amount numeric(10, 2) NULL,
	flag bool DEFAULT false NOT NULL,
	CONSTRAINT things_pkey PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ux_things_org_name ON punchapp_prod.things USING btree (org_id, name) WHERE (name IS NOT NULL);
CREATE INDEX idx_things_org ON punchapp_prod.things USING btree (org_id);
CREATE SEQUENCE punchapp_prod.thing_seq START 1;
INSERT INTO punchapp_prod.things (org_id) VALUES ('seed');
"""


def run_with(tmp_path, migrations: dict, ddl: str = DDL):
    d = tmp_path / "migrations"
    d.mkdir(exist_ok=True)
    for name, body in migrations.items():
        (d / name).write_text(textwrap.dedent(body), encoding="utf-8")
    f = tmp_path / "ddl.sql"
    f.write_text(ddl, encoding="utf-8")
    findings, infos, cat = C.run(f, d)
    return findings


def msgs(findings, level=None):
    return [f"{f.level}: {f.message}" for f in findings if level is None or f.level == level]


def blocking(findings):
    return [f for f in findings if f.level in ("DRIFT", "ERROR")]


# ------------------------------------------------------------------ the real repository
def test_repository_ddl_and_migrations_are_consistent():
    findings, infos, cat = C.run(C.DEFAULT_DDL, C.DEFAULT_MIGRATIONS)
    assert not blocking(findings), "\n".join(str(f) for f in blocking(findings))
    assert len(infos) >= 9
    assert "attendance" in cat.tables and "ux_attendance_one_open" in cat.indexes


def test_repository_cli_exit_code_is_zero():
    assert C.main([]) == 0


def test_the_real_migrations_are_actually_analysed():
    """Guard against a vacuous pass: the extractor must see the objects of the known migrations."""
    _, infos, _ = C.run(C.DEFAULT_DDL, C.DEFAULT_MIGRATIONS)
    by = {i.number: i for i in infos}
    assert by[28].analysable_do >= 1 and by[29].analysable_do >= 2 and by[32].analysable_do >= 1
    kinds = [type(s).__name__ for s, _ in by[31].stmts]
    assert kinds.count("AlterTableStmt") >= 18 and kinds.count("IndexStmt") == 3
    assert by[27].created_tables == {"user_push_tokens"}


# ------------------------------------------------------------------ the happy path of the synthetic DDL
def test_clean_migration_passes(tmp_path):
    f = run_with(tmp_path, {"001_ok.sql": """
        ALTER TABLE things ADD COLUMN IF NOT EXISTS amount numeric(10, 2) NULL;
        ALTER TABLE things ADD COLUMN IF NOT EXISTS flag boolean NOT NULL DEFAULT false;
        CREATE INDEX IF NOT EXISTS idx_things_org ON things USING btree (org_id);
        CREATE SEQUENCE IF NOT EXISTS thing_seq START 1;
        INSERT INTO things (org_id) VALUES ('seed') ON CONFLICT DO NOTHING;
    """})
    assert not blocking(f), msgs(f)


# ------------------------------------------------------------------ drift detection
@pytest.mark.parametrize("sql, expect", [
    ("ALTER TABLE things ADD COLUMN IF NOT EXISTS extra int4 NULL;", "extra is not in the DDL"),
    ("ALTER TABLE things ADD COLUMN IF NOT EXISTS amount numeric(12, 2) NULL;", "type numeric(12,2)"),
    ("ALTER TABLE things ADD COLUMN IF NOT EXISTS amount numeric(10, 2) NOT NULL DEFAULT 0;", "NOT NULL"),
    ("ALTER TABLE things ADD COLUMN IF NOT EXISTS flag bool NOT NULL DEFAULT true;", "DEFAULT true"),
    ("CREATE INDEX IF NOT EXISTS idx_missing ON things USING btree (org_id);", "idx_missing"),
    ("CREATE INDEX IF NOT EXISTS idx_things_org ON things USING btree (org_id, name);", "keys"),
    ("CREATE UNIQUE INDEX IF NOT EXISTS ux_things_org_name ON things USING btree (org_id, name) "
     "WHERE (name IS NOT NULL);\n-- pre-check: HAVING count(*) > 1", None),
    ("CREATE UNIQUE INDEX IF NOT EXISTS ux_things_org_name ON things USING btree (org_id, name);\n"
     "-- HAVING count(*) > 1", "WHERE"),
    ("CREATE INDEX IF NOT EXISTS idx_things_org ON things USING btree (org_id) WHERE (flag);", "WHERE"),
    ("CREATE SEQUENCE IF NOT EXISTS other_seq START 1;", "other_seq"),
    ("CREATE TABLE IF NOT EXISTS gone (id int4 NOT NULL);", "table gone is not in the DDL"),
    ("ALTER TABLE things ALTER COLUMN name SET DEFAULT 'y';", "default"),
    ("INSERT INTO things (org_id, nope) VALUES ('a', 'b') ON CONFLICT DO NOTHING;", "nope"),
    ("ALTER TABLE things ADD COLUMN IF NOT EXISTS extra int4;", "extra"),
])
def test_drift_is_detected(tmp_path, sql, expect):
    f = run_with(tmp_path, {"001_x.sql": sql + "\n"})
    if expect is None:
        assert not blocking(f), msgs(f)
    else:
        assert any(expect in m for m in msgs(blocking(f))), msgs(f)


def test_alter_column_type_in_do_block_is_compared_with_ddl(tmp_path):
    sql = """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE column_name = 'name') THEN
                ALTER TABLE things ALTER COLUMN name TYPE varchar(100);
            END IF;
        END
        $$;
    """
    f = run_with(tmp_path, {"001_x.sql": sql})
    assert any("changes type to varchar(100), DDL has varchar(64)" in m for m in msgs(f, "DRIFT"))
    f = run_with(tmp_path, {"001_x.sql": sql.replace("100", "64")})
    assert not blocking(f)


def test_constraint_checks(tmp_path):
    ddl = DDL.replace("flag bool DEFAULT false NOT NULL,", "flag bool DEFAULT false NOT NULL,\n"
                                                         "\tCONSTRAINT things_amount_check CHECK (amount > 0),")
    ok = """
        DO $$ BEGIN
            ALTER TABLE things ADD CONSTRAINT things_amount_check CHECK (amount > 0);
        END $$;
    """
    assert not blocking(run_with(tmp_path, {"001_x.sql": ok}, ddl))
    bad = ok.replace("amount > 0", "amount > 5")
    assert any("differs" in m for m in msgs(run_with(tmp_path, {"001_x.sql": bad}, ddl), "DRIFT"))
    missing = ok.replace("things_amount_check", "things_other_check")
    assert any("things_other_check" in m for m in msgs(run_with(tmp_path, {"001_x.sql": missing}, ddl), "DRIFT"))


def test_dropped_objects_must_be_absent_from_ddl(tmp_path):
    f = run_with(tmp_path, {"001_x.sql": "DROP INDEX IF EXISTS idx_things_org;\n"})
    assert any("still in the DDL" in m for m in msgs(f, "DRIFT"))
    f = run_with(tmp_path, {"001_x.sql": "DROP INDEX IF EXISTS some_old_index;\n"})
    assert not blocking(f)


def test_enum_values(tmp_path):
    f = run_with(tmp_path, {"001_x.sql": "ALTER TYPE kind_enum ADD VALUE IF NOT EXISTS 'c';\n"})
    assert any("kind_enum.c" in m for m in msgs(f, "DRIFT"))
    f = run_with(tmp_path, {"001_x.sql": "ALTER TYPE kind_enum ADD VALUE IF NOT EXISTS 'b';\n"})
    assert not blocking(f)


def test_seed_rows_must_be_in_ddl_seed_section(tmp_path):
    ins = "INSERT INTO other (a) VALUES (1) ON CONFLICT DO NOTHING;\n"
    ddl = DDL + "CREATE TABLE punchapp_prod.other (a int4 NOT NULL);\n"
    assert any("seed rows for other" in m for m in msgs(run_with(tmp_path, {"001_x.sql": ins}, ddl), "DRIFT"))
    assert not blocking(run_with(tmp_path, {"001_x.sql": ins}, ddl + "INSERT INTO punchapp_prod.other VALUES (1);"))


# ------------------------------------------------------------------ migration sanity
@pytest.mark.parametrize("sql, expect", [
    ("CREATE TABLE things (id int4);", "without IF NOT EXISTS"),
    ("CREATE INDEX idx_things_org ON things USING btree (org_id);", "without IF NOT EXISTS"),
    ("ALTER TABLE things ADD COLUMN amount numeric(10, 2) NULL;", "without IF NOT EXISTS"),
    ("CREATE SEQUENCE thing_seq;", "without IF NOT EXISTS"),
    ("INSERT INTO things (org_id) VALUES ('seed');", "without ON CONFLICT"),
    ("DROP INDEX some_old_index;", "without IF EXISTS"),
    ("DELETE FROM things;", "never delete"),
    ("DROP TABLE IF EXISTS things;", "never delete"),
    ("ALTER TABLE things DROP COLUMN IF EXISTS name;", "never delete"),
    ("BEGIN;\nALTER TABLE things ADD COLUMN IF NOT EXISTS amount numeric(10, 2) NULL;\nCOMMIT;", "BEGIN/COMMIT"),
    ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_things_org ON things USING btree (org_id);", "CONCURRENTLY"),
    ("ALTER TABLE things ADD CONSTRAINT things_pkey PRIMARY KEY (id);", "outside a guarded DO block"),
    ("CREATE UNIQUE INDEX IF NOT EXISTS ux_things_org_name ON things USING btree (org_id, name) "
     "WHERE (name IS NOT NULL);", "pre-check"),
])
def test_sanity_rules(tmp_path, sql, expect):
    f = run_with(tmp_path, {"001_x.sql": sql + "\n"})
    assert any(expect in m for m in msgs(f, "ERROR")), msgs(f)


def test_unique_index_on_table_created_in_same_migration_needs_no_precheck(tmp_path):
    ddl = DDL + ("CREATE TABLE punchapp_prod.fresh (a int4 NOT NULL, b int4 NOT NULL);\n"
                 "CREATE UNIQUE INDEX ux_fresh ON punchapp_prod.fresh USING btree (a, b);\n")
    sql = """
        CREATE TABLE IF NOT EXISTS fresh (a int4 NOT NULL, b int4 NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_fresh ON fresh USING btree (a, b);
    """
    assert not blocking(run_with(tmp_path, {"001_x.sql": sql}, ddl))


def test_ordering_dependency_on_later_migration(tmp_path):
    ddl = DDL + "CREATE TABLE punchapp_prod.late (a int4 NOT NULL);\n"
    f = run_with(tmp_path, {
        "001_early.sql": "ALTER TABLE late ADD COLUMN IF NOT EXISTS a int4 NOT NULL;\n",
        "002_late.sql": "CREATE TABLE IF NOT EXISTS late (a int4 NOT NULL);\n",
    }, ddl)
    assert any("created later by migration 002" in m for m in msgs(f, "ERROR"))


def test_syntax_error_and_duplicate_numbers(tmp_path):
    f = run_with(tmp_path, {"001_a.sql": "ALTER TABEL things;\n", "001_b.sql": "SELECT 1;\n"})
    m = msgs(f, "ERROR")
    assert any("does not parse" in x for x in m)
    assert any("duplicate migration number" in x for x in m)


def test_ddl_duplicate_index_name_is_reported(tmp_path):
    ddl = DDL + "CREATE INDEX idx_things_org ON punchapp_prod.things USING btree (name);\n"
    f = run_with(tmp_path, {"001_x.sql": "SELECT 1;\n"}, ddl)
    assert any("used twice" in m for m in msgs(f, "ERROR"))


def test_normalisation_helpers():
    assert C.norm_sql("CAST('pending' AS varchar)") == "'pending'"
    assert C.norm_sql("CURRENT_TIMESTAMP") == C.norm_sql("now()")
    assert C.norm_sql("punchapp_prod.f(1)") == "f(1)"
