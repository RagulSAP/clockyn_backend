"""DB-free tests for migrate.py: file discovery / order, pending calculation, baseline decision, dry-run output,
and the apply loop over a fake connection (transaction semantics, recording, locking, failure handling).
Run: python -m pytest tests/test_migrate.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import migrate  # noqa: E402
from migrate import Migration, MigrationError  # noqa: E402


# ---------------------------------------------------------------------------------------------------------------
# Fake connection: a tiny model of one schema (tables + schema_migrations) with real commit / rollback semantics
# ---------------------------------------------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        c = self.conn
        flat = " ".join(sql.split())
        c.executed.append((flat, params))
        if c.fail_on and c.fail_on in flat:
            raise RuntimeError("boom: " + c.fail_on)
        if "FROM pg_namespace" in flat:
            self._rows = [(1,)] if c.schema_exists else []
        elif "FROM information_schema.tables" in flat:
            _schema, table = params
            self._rows = [(1,)] if table in c.tables else []
        elif flat.startswith("CREATE SCHEMA"):
            c.schema_exists = True
        elif flat.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            c.tables.add("schema_migrations")
        elif flat.startswith("SELECT version FROM schema_migrations"):
            self._rows = [(v,) for v in sorted(c.applied)]
        elif flat.startswith("INSERT INTO schema_migrations"):
            c.pending_inserts.append(params[0])
        elif flat.startswith("SELECT pg_advisory_lock"):
            c.locked = True
        elif flat.startswith("SELECT pg_advisory_unlock"):
            c.locked = False
        elif flat.startswith("-- MIGRATION") or flat.startswith("-- BASELINE"):
            c.pending_effects.append(flat.split()[2])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConn:
    def __init__(self, schema_exists=True, tables=(), applied=(), fail_on=None):
        self.schema_exists = schema_exists
        self.tables = set(tables)
        self.applied = set(applied)
        self.fail_on = fail_on
        self.executed = []
        self.pending_inserts = []
        self.pending_effects = []
        self.effects = []       # committed file effects, in order
        self.commits = 0
        self.rollbacks = 0
        self.locked = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.applied.update(self.pending_inserts)
        self.effects.extend(self.pending_effects)
        self.pending_inserts, self.pending_effects = [], []

    def rollback(self):
        self.rollbacks += 1
        self.pending_inserts, self.pending_effects = [], []

    def sqls(self):
        return [s for s, _ in self.executed]


@pytest.fixture
def mdir(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    for n in ("001_a", "002_b", "010_c"):
        (d / f"{n}.sql").write_text(f"-- MIGRATION {n}\n", encoding="utf-8")
    base = tmp_path / "baseline.sql"
    base.write_text("-- BASELINE punchapp_prod\nCREATE TABLE punchapp_prod.users (x int);\n", encoding="utf-8")
    return d, base


def run(conn, mdir, schema="punchapp_prod", dry_run=False):
    d, base = mdir
    lines = []
    plan = migrate.run_migrations(conn, schema, dry_run=dry_run, migrations_dir=d, baseline_path=base, out=lines.append)
    return plan, lines


# ---------------------------------------------------------------------------------------------------------------
# File discovery and naming
# ---------------------------------------------------------------------------------------------------------------
def test_every_real_migration_file_name_parses():
    files = sorted(migrate.MIGRATIONS_DIR.glob("*.sql"))
    assert files, "no migration files found"
    for f in files:
        m = migrate.parse_migration_name(f.name)
        assert m is not None, f"{f.name} does not match NNN_description.sql"
        assert m.version == f.stem
    found = migrate.discover_migrations()          # raises on a bad name or a duplicate number
    numbers = [m.number for m in found]
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    assert {"024_otp_send_log", "025_org_holidays_unique_scope"} <= {m.version for m in found}


@pytest.mark.parametrize("name,number", [("001_x.sql", 1), ("024_otp_send_log.sql", 24), ("1234_a-b.c.sql", 1234)])
def test_parse_good_names(name, number):
    m = migrate.parse_migration_name(name)
    assert m.number == number and m.version == name[:-4]


@pytest.mark.parametrize("name", ["x_001.sql", "001.sql", "001_.sql", "001_x.txt", "README.md", "001 x.sql", "_001_x.sql"])
def test_parse_bad_names(name):
    assert migrate.parse_migration_name(name) is None


def test_discovery_orders_numerically_not_alphabetically(tmp_path):
    for n in ("100_z", "2_b", "010_y", "9_a"):
        (tmp_path / f"{n}.sql").write_text("select 1;")
    assert [m.number for m in migrate.discover_migrations(tmp_path)] == [2, 9, 10, 100]


def test_discovery_rejects_duplicate_number_and_bad_name(tmp_path):
    (tmp_path / "007_a.sql").write_text("x")
    (tmp_path / "07_b.sql").write_text("x")
    with pytest.raises(MigrationError, match="duplicate migration number 007"):
        migrate.discover_migrations(tmp_path)
    (tmp_path / "07_b.sql").unlink()
    (tmp_path / "notes.sql").write_text("x")
    with pytest.raises(MigrationError, match="notes.sql"):
        migrate.discover_migrations(tmp_path)


def test_pending_calculation():
    ms = [Migration(i, f"{i:03d}_m", Path(f"{i:03d}_m.sql")) for i in (1, 2, 3)]
    assert migrate.pending_migrations(ms, set()) == ms
    assert migrate.pending_migrations(ms, {"001_m", "003_m"}) == [ms[1]]
    assert migrate.pending_migrations(ms, {"001_m", "002_m", "003_m", "999_gone"}) == []


# ---------------------------------------------------------------------------------------------------------------
# Baseline decision
# ---------------------------------------------------------------------------------------------------------------
MS = [Migration(1, "001_a", Path("001_a.sql")), Migration(2, "002_b", Path("002_b.sql"))]


def test_decide_baseline_when_no_users_table():
    p = migrate.decide_plan(False, set(), MS)
    assert p.kind == "baseline" and p.apply_baseline and p.to_apply == [] and p.to_record == MS


def test_decide_legacy_when_users_exists_but_nothing_recorded():
    p = migrate.decide_plan(True, set(), MS)
    assert p.kind == "legacy" and not p.apply_baseline and p.to_apply == MS and p.to_record == []


def test_decide_incremental_and_noop():
    p = migrate.decide_plan(True, {"001_a"}, MS)
    assert p.kind == "incremental" and p.to_apply == [MS[1]]
    p = migrate.decide_plan(True, {"001_a", "002_b"}, MS)
    assert p.kind == "noop" and p.to_apply == [] and p.to_record == [] and not p.apply_baseline


def test_decide_rejects_recorded_versions_without_users_table():
    with pytest.raises(MigrationError, match="inconsistent"):
        migrate.decide_plan(False, {"001_a"}, MS)


def test_adapt_baseline_sql_replaces_only_the_schema_word():
    sql = "CREATE SCHEMA IF NOT EXISTS punchapp_prod;\nCREATE TABLE punchapp_prod.users (); -- punchapp_prod_x stays"
    assert migrate.adapt_baseline_sql(sql, "punchapp_prod") == sql
    out = migrate.adapt_baseline_sql(sql, "clockyn_qa")
    assert "punchapp_prod" not in out.replace("punchapp_prod_x", "")
    assert "CREATE TABLE clockyn_qa.users ()" in out and "punchapp_prod_x" in out
    with pytest.raises(MigrationError):
        migrate.adapt_baseline_sql(sql, "bad;name")


@pytest.mark.parametrize("bad", [None, "", "1abc", "a-b", "a b", 'a"b', "a;drop"])
def test_schema_name_validation(bad):
    with pytest.raises(MigrationError):
        migrate.validate_schema(bad)


# ---------------------------------------------------------------------------------------------------------------
# Output text
# ---------------------------------------------------------------------------------------------------------------
def test_format_plan_and_status_output():
    plan = migrate.decide_plan(True, {"001_a", "777_old"}, MS)
    lines = migrate.format_plan(plan, "s1", {"001_a", "777_old"}, MS, dry_run=True)
    assert lines[0] == "schema: s1   (dry run: nothing will be written)"
    assert lines[1].startswith("mode: incremental")
    assert "  apply   002_b" in lines and "  note    777_old is recorded as applied but has no file in migrations/" in lines
    base = migrate.format_plan(migrate.decide_plan(False, set(), MS), "s1", set(), MS, dry_run=False)
    assert "  apply   punchapp_ddl_latest.sql (baseline)" in base
    assert "  record  001_a (covered by the baseline)" in base and "dry run" not in base[0]
    st = migrate.format_status("s1", {"001_a", "777_old"}, MS)
    assert st == ["schema: s1", "  applied 001_a", "  pending 002_b", "  applied 777_old   (no file)", "1 applied, 1 pending"]


# ---------------------------------------------------------------------------------------------------------------
# The apply loop over the fake connection
# ---------------------------------------------------------------------------------------------------------------
def test_dry_run_reads_only_and_prints_the_plan(mdir):
    conn = FakeConn(schema_exists=True, tables={"users", "schema_migrations"}, applied={"001_a"})
    plan, lines = run(conn, mdir, dry_run=True)
    assert plan.kind == "incremental" and [m.version for m in plan.to_apply] == ["002_b", "010_c"]
    assert "(dry run" in lines[0] and "  apply   002_b" in lines and "  apply   010_c" in lines
    sqls = conn.sqls()
    assert not any(s.startswith(("CREATE", "INSERT", "ALTER", "DROP")) or "advisory" in s or "-- MIGRATION" in s for s in sqls)
    assert conn.effects == [] and conn.applied == {"001_a"}


def test_dry_run_on_empty_database_does_not_create_anything(mdir):
    conn = FakeConn(schema_exists=False)
    plan, lines = run(conn, mdir, dry_run=True)
    assert plan.kind == "baseline" and "  apply   punchapp_ddl_latest.sql (baseline)" in lines
    assert not conn.schema_exists and "schema_migrations" not in conn.tables
    assert not any(s.startswith(("CREATE", "INSERT")) for s in conn.sqls())


def test_empty_database_applies_baseline_then_records_every_migration(mdir):
    conn = FakeConn(schema_exists=False)
    plan, _lines = run(conn, mdir)
    assert plan.kind == "baseline"
    assert conn.schema_exists and "schema_migrations" in conn.tables
    assert conn.effects == ["punchapp_prod"]                      # baseline ran; no individual migration ran
    assert conn.applied == {"001_a", "002_b", "010_c"}
    assert not conn.locked
    assert conn.sqls()[0].startswith("SELECT pg_advisory_lock") and conn.sqls()[-1].startswith("SELECT pg_advisory_unlock")


def test_baseline_is_pointed_at_the_target_schema(mdir):
    conn = FakeConn(schema_exists=True)
    run(conn, mdir, schema="clockyn_qa")
    assert any(s.startswith("-- BASELINE clockyn_qa") for s in conn.sqls())
    assert 'SET search_path TO "clockyn_qa"' in conn.sqls()


def test_legacy_database_runs_every_migration_in_order_and_records_them(mdir):
    conn = FakeConn(schema_exists=True, tables={"users"})
    plan, _lines = run(conn, mdir)
    assert plan.kind == "legacy"
    assert conn.effects == ["001_a", "002_b", "010_c"] and conn.applied == {"001_a", "002_b", "010_c"}
    assert "schema_migrations" in conn.tables


def test_incremental_runs_only_pending_and_second_run_is_a_noop(mdir):
    conn = FakeConn(schema_exists=True, tables={"users", "schema_migrations"}, applied={"001_a", "002_b"})
    run(conn, mdir)
    assert conn.effects == ["010_c"] and conn.applied == {"001_a", "002_b", "010_c"}
    conn.effects.clear()
    n = len(conn.executed)
    plan, lines = run(conn, mdir)
    assert plan.kind == "noop" and conn.effects == []
    assert not any("-- MIGRATION" in s or s.startswith("INSERT") for s, _ in conn.executed[n:])
    assert any("nothing to do" in line for line in lines)


def test_failure_rolls_back_that_file_keeps_earlier_ones_and_releases_the_lock(mdir):
    conn = FakeConn(schema_exists=True, tables={"users", "schema_migrations"}, fail_on="-- MIGRATION 002_b")
    with pytest.raises(RuntimeError, match="boom"):
        run(conn, mdir)
    assert conn.applied == {"001_a"}                 # 002_b not recorded, 010_c never started
    assert conn.effects == ["001_a"]
    assert not any("010_c" in s for s in conn.sqls())
    assert conn.rollbacks >= 1 and not conn.locked


def test_each_migration_and_its_record_commit_together(mdir):
    conn = FakeConn(schema_exists=True, tables={"users", "schema_migrations"})
    run(conn, mdir)
    sqls = conn.sqls()
    for v in ("001_a", "002_b", "010_c"):
        i = sqls.index(f"-- MIGRATION {v}")
        assert sqls[i + 1].startswith("INSERT INTO schema_migrations") and conn.executed[i + 1][1] == (v,)


def test_status_reports_applied_and_pending(mdir):
    conn = FakeConn(schema_exists=True, tables={"users", "schema_migrations"}, applied={"001_a"})
    out = []
    lines = migrate.status(conn, "punchapp_prod", migrations_dir=mdir[0], out=out.append)
    assert lines == out and "  pending 002_b" in lines and "1 applied, 2 pending" in lines[-1]
    assert not any(s.startswith(("CREATE", "INSERT")) for s in conn.sqls())


def test_status_on_unmigrated_schema(mdir):
    conn = FakeConn(schema_exists=False)
    lines = migrate.status(conn, "x", migrations_dir=mdir[0], out=lambda _l: None)
    assert lines[-1] == "0 applied, 3 pending"


# ---------------------------------------------------------------------------------------------------------------
# Start-up hook: guarded, never raises
# ---------------------------------------------------------------------------------------------------------------
def test_startup_hook_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RUN_MIGRATIONS", raising=False)
    monkeypatch.setattr(migrate, "connect_from_env", lambda: pytest.fail("must not connect"))
    assert migrate.run_startup_migrations() is False


def test_startup_hook_never_crashes_startup(monkeypatch, caplog):
    monkeypatch.setenv("RUN_MIGRATIONS", "1")
    monkeypatch.setenv("DB_SCHEMA", "punchapp_prod")

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(migrate, "connect_from_env", boom)
    with caplog.at_level("ERROR", logger="migrate"):
        assert migrate.run_startup_migrations() is False
    assert "applying migrations failed" in caplog.text


def test_startup_hook_runs_the_same_function(monkeypatch):
    monkeypatch.setenv("RUN_MIGRATIONS", "true")
    monkeypatch.setenv("DB_SCHEMA", "punchapp_prod")
    calls = []

    class ClosingConn(FakeConn):
        def close(self):
            calls.append("closed")
    conn = ClosingConn(schema_exists=True, tables={"users", "schema_migrations"},
                       applied={m.version for m in migrate.discover_migrations()})
    monkeypatch.setattr(migrate, "connect_from_env", lambda: conn)
    assert migrate.run_startup_migrations() is True and calls == ["closed"]
