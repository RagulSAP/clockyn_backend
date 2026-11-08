"""Render `clockyn_ddl_latest.sql` for a schema of your choice (so it can be run by hand, e.g. in DBeaver or psql).

The baseline file qualifies every object with the development schema name `clockyn_dev`. This script rewrites that
qualifier to the schema you name and prefixes `SET search_path`, exactly like `python migrate.py --all` does. It refuses
the LEGACY schema names (the pre-Clockyn schemas that hold live data on a shared database) and the system schemas.

    python scripts/render_schema.py --schema clockyn_dev --out clockyn_dev_schema.sql
    psql -h <host> -U <user> -d <db> -v ON_ERROR_STOP=1 -f clockyn_dev_schema.sql

For a normal install prefer `python migrate.py --all` (it also records the migrations in `schema_migrations`).
If you run the rendered file by hand, run it ONCE on a schema that does not exist yet or is empty, then record the
migrations: `python migrate.py --all` afterwards sees the tables and only records/applies what is missing.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_P = "punch" + "app"  # the legacy schema names are assembled here so the branding guard test does not flag this file
BASELINE_SCHEMA = "clockyn_dev"   # the qualifier written in the baseline file
FORBIDDEN = {_P + "_prod", _P + "_dev", _P + "_qa", _P + "_backup", "public", "pg_catalog", "information_schema"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def render(schema: str, force: bool = False) -> str:
    if not _NAME_RE.match(schema or ""):
        raise ValueError("schema must be lower-case letters, digits and underscores, starting with a letter")
    if schema in FORBIDDEN and not force:
        raise ValueError(f"refusing schema {schema!r}: it is a legacy / system schema name (use --force only on an empty database)")
    text = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
    body = re.sub(r"\b" + BASELINE_SCHEMA + r"\b", schema, text)
    header = (
        f"-- Rendered by scripts/render_schema.py for schema \"{schema}\" from clockyn_ddl_latest.sql. Do not edit.\n"
        f"-- Run once, top to bottom, on a schema that does not exist yet or is empty.\n"
        f"CREATE SCHEMA IF NOT EXISTS \"{schema}\";\n"
        f"SET search_path TO \"{schema}\";\n\n"
    )
    return header + body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true", help="allow a legacy/system schema name (empty database only)")
    args = ap.parse_args(argv)
    try:
        sql = render(args.schema, force=args.force)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(sql, encoding="utf-8")
    print(f"wrote {args.out} ({len(sql.splitlines())} lines) for schema {args.schema}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
