#!/usr/bin/env python3
"""Database migration runner (idempotent, ordered, one transaction per migration).

Usage
    python migrate.py --all                 apply every pending migrations/NNN_*.sql, in numeric order
    python migrate.py --all --dry-run       show what --all would do; reads the database, writes nothing
    python migrate.py --status              list applied and pending migrations
    python migrate.py <schema> <file.sql>   legacy form: run one file on <schema> (not recorded)

Environment (same variables as core_functions/db_function.py)
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD   connection (libpq variables such as PGSSLMODE also work)
    DB_SCHEMA                                         target schema (becomes the search_path); --schema overrides
    RUN_MIGRATIONS=1                                  app.py runs the same function at startup (never fatal)

Behaviour
    * Applied versions are recorded in <schema>.schema_migrations(version text primary key, applied_at timestamptz).
    * Schema without a `users` table and nothing recorded: the baseline file punchapp_ddl_latest.sql is applied first
      (it already contains the effect of every migration file), then every existing migration file is recorded
      as applied without being executed.
    * Schema that already has `users` but nothing recorded (a legacy-shaped database): every migration file is
      executed in order (they are all idempotent) and recorded.
    * Otherwise: only the files not yet recorded are executed.
    * A pg advisory lock keeps two processes (for example two containers at start-up) from migrating at once.
    * Each migration runs in its own transaction together with its schema_migrations row; a failure rolls that file
      back and stops the run (earlier files stay applied).
"""
import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

try:  # python-dotenv and psycopg2 are only needed for a real run; the pure helpers work without a database
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger("migrate")

ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = ROOT / "migrations"
BASELINE_FILE = ROOT / "punchapp_ddl_latest.sql"
BASELINE_SCHEMA = "punchapp_prod"          # the schema qualifier written in the baseline file
MARKER_TABLE = "users"                     # present in every schema that was created from the baseline
VERSION_TABLE = "schema_migrations"
ADVISORY_LOCK_KEY = 7262026001             # arbitrary constant shared by every migrator process

_FILE_RE = re.compile(r"^(\d+)_[A-Za-z0-9_.\-]+\.sql$")
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MigrationError(Exception):
    """Configuration or consistency problem (bad file name, duplicate number, unusable schema name)."""


@dataclass(frozen=True)
class Migration:
    number: int
    version: str        # file name without ".sql", e.g. "024_otp_send_log"
    path: Path


@dataclass
class Plan:
    kind: str                                           # baseline | legacy | incremental | noop
    apply_baseline: bool = False
    to_apply: List[Migration] = field(default_factory=list)    # executed, then recorded
    to_record: List[Migration] = field(default_factory=list)   # recorded only (covered by the baseline)


# ----------------------------------------------------------------------------------------------------------------
# Pure helpers (no database)
# ----------------------------------------------------------------------------------------------------------------
def validate_schema(schema: Optional[str]) -> str:
    if not schema or not _SCHEMA_RE.match(schema):
        raise MigrationError("schema name is missing or has invalid characters (letters, digits, underscore only)")
    return schema


def parse_migration_name(name: str) -> Optional[Migration]:
    """`024_otp_send_log.sql` -> Migration(24, '024_otp_send_log', ...); None when the name is not a migration."""
    m = _FILE_RE.match(name)
    if not m:
        return None
    return Migration(int(m.group(1)), name[:-4], Path(name))


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> List[Migration]:
    """Every NNN_*.sql in `directory`, sorted by number. Two files with the same number are an error."""
    found: List[Migration] = []
    for p in sorted(Path(directory).glob("*.sql")):
        parsed = parse_migration_name(p.name)
        if parsed is None:
            raise MigrationError(f"{p.name}: file name must look like NNN_description.sql")
        found.append(Migration(parsed.number, parsed.version, p))
    found.sort(key=lambda m: (m.number, m.version))
    seen = {}
    for m in found:
        if m.number in seen:
            raise MigrationError(f"duplicate migration number {m.number:03d}: {seen[m.number]} and {m.version}")
        seen[m.number] = m.version
    return found


def pending_migrations(migrations: List[Migration], applied: Set[str]) -> List[Migration]:
    return [m for m in migrations if m.version not in applied]


def decide_plan(has_marker_table: bool, applied: Set[str], migrations: List[Migration]) -> Plan:
    """What to do for a schema in the given state (see the module docstring)."""
    if not has_marker_table:
        if applied:
            raise MigrationError(
                f"{VERSION_TABLE} has {len(applied)} row(s) but table '{MARKER_TABLE}' does not exist: "
                "the schema is inconsistent, fix it by hand before migrating")
        return Plan("baseline", apply_baseline=True, to_record=list(migrations))
    if not applied:
        return Plan("legacy", to_apply=list(migrations))
    todo = pending_migrations(migrations, applied)
    return Plan("incremental" if todo else "noop", to_apply=todo)


def adapt_baseline_sql(sql: str, schema: str) -> str:
    """The baseline file qualifies every object with punchapp_prod; point it at the target schema."""
    validate_schema(schema)
    if schema == BASELINE_SCHEMA:
        return sql
    return re.sub(r"\b" + BASELINE_SCHEMA + r"\b", schema, sql)


def format_plan(plan: Plan, schema: str, applied: Set[str], migrations: List[Migration], dry_run: bool) -> List[str]:
    lines = [f"schema: {schema}" + ("   (dry run: nothing will be written)" if dry_run else "")]
    reasons = {
        "baseline": "no users table: apply the baseline file, then record every migration as applied",
        "legacy": "users table exists but nothing is recorded: run every migration in order and record it",
        "incremental": f"{len(plan.to_apply)} pending migration(s)",
        "noop": "nothing to do, every migration is already applied",
    }
    lines.append(f"mode: {plan.kind} - {reasons[plan.kind]}")
    if plan.apply_baseline:
        lines.append(f"  apply   {BASELINE_FILE.name} (baseline)")
    for m in plan.to_apply:
        lines.append(f"  apply   {m.version}")
    for m in plan.to_record:
        lines.append(f"  record  {m.version} (covered by the baseline)")
    known = {m.version for m in migrations}
    for v in sorted(applied - known):
        lines.append(f"  note    {v} is recorded as applied but has no file in migrations/")
    return lines


def format_status(schema: str, applied: Set[str], migrations: List[Migration]) -> List[str]:
    lines = [f"schema: {schema}"]
    for m in migrations:
        lines.append(f"  {'applied' if m.version in applied else 'pending':8s}{m.version}")
    known = {m.version for m in migrations}
    for v in sorted(applied - known):
        lines.append(f"  {'applied':8s}{v}   (no file)")
    n_pending = len(pending_migrations(migrations, applied))
    lines.append(f"{len(migrations) - n_pending} applied, {n_pending} pending")
    return lines


# ----------------------------------------------------------------------------------------------------------------
# Database part (takes any DB-API connection: a fake in the tests)
# ----------------------------------------------------------------------------------------------------------------
def _table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s", (schema, table))
    return cur.fetchone() is not None


def _schema_exists(cur, schema: str) -> bool:
    cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
    return cur.fetchone() is not None


def read_state(conn, schema: str, create: bool):
    """(has_marker_table, applied_versions). With create=True the schema and schema_migrations are created."""
    cur = conn.cursor()
    try:
        if not _schema_exists(cur, schema):
            if not create:
                return False, set()
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.commit()
        cur.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        has_marker = _table_exists(cur, schema, MARKER_TABLE)
        if create:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} ("
                        "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            conn.commit()
        elif not _table_exists(cur, schema, VERSION_TABLE):
            conn.rollback()
            return has_marker, set()
        cur.execute(f"SELECT version FROM {VERSION_TABLE}")
        applied = {r[0] if not isinstance(r, dict) else r["version"] for r in cur.fetchall()}
        conn.commit()
        return has_marker, applied
    finally:
        cur.close()


def _record(cur, version: str):
    cur.execute(f"INSERT INTO {VERSION_TABLE} (version) VALUES (%s) ON CONFLICT (version) DO NOTHING", (version,))


def _apply_sql(conn, sql: str, versions: List[str]):
    """Run `sql` and record `versions` in one transaction."""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        for v in versions:
            _record(cur, v)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def run_migrations(conn, schema: str, dry_run: bool = False, migrations_dir: Path = MIGRATIONS_DIR,
                   baseline_path: Path = BASELINE_FILE, out=print) -> Plan:
    """Bring `schema` up to date over an open connection. Returns the plan that was (or, for dry_run, would be) executed."""
    validate_schema(schema)
    migrations = discover_migrations(migrations_dir)
    locked = False
    try:
        if not dry_run:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            cur.close()
            conn.commit()
            locked = True
        has_marker, applied = read_state(conn, schema, create=not dry_run)
        plan = decide_plan(has_marker, applied, migrations)
        for line in format_plan(plan, schema, applied, migrations, dry_run):
            out(line)
        if dry_run:
            return plan
        if plan.apply_baseline:
            sql = adapt_baseline_sql(_read(baseline_path), schema)
            _apply_sql(conn, sql, [m.version for m in plan.to_record])
            out(f"  done    baseline applied, {len(plan.to_record)} migration(s) recorded")
        for m in plan.to_apply:
            _apply_sql(conn, _read(m.path), [m.version])
            out(f"  done    {m.version}")
        return plan
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        if locked:
            try:
                cur = conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
                cur.close()
                conn.commit()
            except Exception:
                pass


def status(conn, schema: str, migrations_dir: Path = MIGRATIONS_DIR, out=print) -> List[str]:
    validate_schema(schema)
    migrations = discover_migrations(migrations_dir)
    _, applied = read_state(conn, schema, create=False)
    conn.rollback()
    lines = format_status(schema, applied, migrations)
    for line in lines:
        out(line)
    return lines


def connect_from_env():
    """Same DB_* variables as db_function.py; libpq reads PGSSLMODE and friends by itself."""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 5432)),
        options="-c timezone=UTC",
    )


def run_all_from_env(schema: Optional[str] = None, dry_run: bool = False, out=print) -> Plan:
    schema = schema or os.getenv("DB_SCHEMA")
    validate_schema(schema)
    conn = connect_from_env()
    try:
        return run_migrations(conn, schema, dry_run=dry_run, out=out)
    finally:
        conn.close()


def run_startup_migrations() -> bool:
    """app.py start-up hook: runs only when RUN_MIGRATIONS=1, and never raises (the error is logged)."""
    if os.getenv("RUN_MIGRATIONS", "").strip().lower() not in ("1", "true", "yes"):
        return False
    try:
        run_all_from_env(out=lambda line: logger.info("migrate: %s", line))
        return True
    except Exception:
        logger.exception("RUN_MIGRATIONS=1 but applying migrations failed; the app keeps starting")
        return False


def run_migration(schema: str, migration_file: str):
    """Legacy form: run one file on one schema (not recorded in schema_migrations)."""
    validate_schema(schema)
    conn = connect_from_env()
    try:
        cursor = conn.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute(_read(Path(migration_file)))
        conn.commit()
        print(f"Migration applied to {schema} schema: {migration_file}")
    except Exception as e:
        conn.rollback()
        print(f"Migration failed on {schema} schema: {e}")
        raise
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Clockyn database migrator")
    ap.add_argument("--all", action="store_true", help="apply every pending migration in order")
    ap.add_argument("--dry-run", action="store_true", help="with --all: show the plan, write nothing")
    ap.add_argument("--status", action="store_true", help="list applied and pending migrations")
    ap.add_argument("--schema", help="target schema (default: DB_SCHEMA)")
    ap.add_argument("legacy", nargs="*", help="legacy form: <schema> <migration_file>")
    args = ap.parse_args(argv)

    try:
        if args.status:
            schema = args.schema or os.getenv("DB_SCHEMA")
            conn = connect_from_env()
            try:
                status(conn, schema)
            finally:
                conn.close()
            return 0
        if args.all or args.dry_run:
            run_all_from_env(args.schema, dry_run=args.dry_run)
            return 0
        if len(args.legacy) == 2:
            run_migration(args.legacy[0], args.legacy[1])
            return 0
    except MigrationError as e:
        print(f"error: {e}")
        return 2
    except Exception as e:
        print(f"error: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
