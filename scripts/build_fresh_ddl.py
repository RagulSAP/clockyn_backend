"""Build `clockyn_ddl_fresh.sql` (every CREATE statement and the default INSERTs, for a NEW database) from `clockyn_ddl_latest.sql`.

`clockyn_ddl_latest.sql` is the baseline the migration runner uses and it also carries "run once on existing databases" blocks at
its end: ALTERs, the table renames and the extra 6 / 12 month plan rows. A brand new database needs none of those ALTERs (the
CREATE TABLE statements already have the columns and the seed already has the rows), but it does need the tables that are
created in those end blocks. This script keeps the CREATE blocks, moves them in front of the seed data, drops every statement that
only exists to change an existing database, and writes a header made for a new database.

    python scripts/build_fresh_ddl.py            # rewrite clockyn_ddl_fresh.sql
    python scripts/build_fresh_ddl.py --check    # exit 1 when clockyn_ddl_fresh.sql is out of date (used by the tests)

Edit `clockyn_ddl_latest.sql`, then run this script. Do not edit `clockyn_ddl_fresh.sql` by hand.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "clockyn_ddl_latest.sql"
FRESH = ROOT / "clockyn_ddl_fresh.sql"

BLOCK = re.compile(r"^-- ─── ", re.M)            # every "run once on existing databases" block starts with this marker
RULE = "-- " + "=" * 76

HEADER = f"""{RULE}
-- Clockyn backend schema for a NEW database: every CREATE statement and the default INSERT rows
{RULE}
-- Generated from clockyn_ddl_latest.sql by scripts/build_fresh_ddl.py. Do not edit by hand: change clockyn_ddl_latest.sql and
-- run the script again (a test fails when this file is out of date).
--
-- This file has only CREATE statements (the foreign keys are added with ALTER TABLE ... ADD CONSTRAINT right after the tables) and
-- idempotent INSERT seed rows. It has nothing that changes an existing table: no ADD COLUMN, no RENAME, no DROP.
-- For an EXISTING database use the "run once on existing databases" blocks at the end of clockyn_ddl_latest.sql instead.
--
-- HOW TO RUN (a new, empty database; run the whole file once, top to bottom)
--
--   1. Create the database (PostgreSQL 13 or newer; the code uses gen_random_uuid()):
--          createdb -h <host> -p <port> -U <user> <dbname>          or, in psql:   CREATE DATABASE <dbname>;
--
--   2. Load this file; it stops at the first error:
--          psql -h <host> -p <port> -U <user> -d <dbname> -v ON_ERROR_STOP=1 -f clockyn_ddl_fresh.sql
--      Python / psycopg2 (the file has no psql meta-commands, a single execute() works):
--          with conn, conn.cursor() as cur: cur.execute(open("clockyn_ddl_fresh.sql", encoding="utf-8").read())
--
--   3. Point the backend at it:  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME=<dbname>, DB_SCHEMA=clockyn_dev
--      (the app runs "SET search_path TO <DB_SCHEMA>" on every connection).
--      A different schema name: replace every  clockyn_dev  in this file with it before running, or use
--          python scripts/render_schema.py --schema <name> --out file.sql      (renders clockyn_ddl_latest.sql the same way)
--
--   4. Optional, once: python migrate.py --all    (records the migrations as applied; everything in them is already in this file)
--
-- Layout: 1 schema, 2 enum types, 3 tables + indexes, 4 foreign keys, 5 sequences, 6 more tables (support, chat, FAQ,
-- clickstream, Refer & Earn), 7 default data (roles, plans with their 1 / 6 / 12 month prices, manager permissions, app versions).
-- The default data is idempotent. Rows marked "OWNER:" (plan prices) are placeholders: set the real values.
-- Not seeded on purpose (you supply them): payment keys (gen_razorpay_key), coupons, support subjects, operators (gen_admin_login),
-- the starter FAQs (FAQ.sql) and the guide videos (support_video).
{RULE}
"""


def _title(block: str) -> str:
    return block.splitlines()[0][len("-- ─── "):]


def build(latest_text: str) -> str:
    text = latest_text.replace("\r\n", "\n")
    first_block = BLOCK.search(text)
    seed_banner = text.index(f"{RULE}\n-- 7. SEED DATA") if f"{RULE}\n-- 7. SEED DATA" in text else -1
    if first_block is None or seed_banner < 0:
        raise SystemExit("clockyn_ddl_latest.sql no longer has the expected '-- ─── ' blocks / '7. SEED DATA' banner")

    body_start = text.index("-- ----------------------------------------------------------------------------\n-- 1. Schema")
    main_before_seed = text[body_start:seed_banner]
    seed = text[seed_banner:first_block.start()]
    appendix = text[first_block.start():]

    blocks = [b for b in re.split(r"(?m)^(?=-- ─── )", appendix) if b.strip()]
    created = []
    for block in blocks:
        title = _title(block)
        if not title.startswith("CREATE:"):
            continue                                              # ALTER / ALTER + INSERT / RENAME: existing databases only
        lines = []
        skip_comment = False
        for line in block.splitlines():
            if re.match(r"(?i)ALTER\s+TABLE\b", line):
                if lines and lines[-1].startswith("-- for a table created before"):
                    lines.pop()
                continue
            lines.append(line)
        block = "\n".join(lines).rstrip() + "\n"
        block = re.sub(r"; run once on existing databases\)", ")", block, count=1)
        block = re.sub(r" run once on existing databases\)", ")", block, count=1)
        block = block.replace(
            "the ALTER TABLE statements for an\n-- existing database are at the end of this block.", "").rstrip("\n") + "\n"
        block = block.replace("-- (two new nullable columns) are part of the gen_orders / gen_invoices definitions above; ",
                              "-- (two nullable columns) are part of the gen_orders / gen_invoices definitions above.\n-- ") \
            if "-- (two new nullable columns)" in block else block
        created.append(block)

    more_tables = (
        "-- ----------------------------------------------------------------------------\n"
        "-- 6b. More tables: Video Hub, Live Chat, FAQ, clickstream, Refer & Earn\n"
        "-- ----------------------------------------------------------------------------\n"
        + "\n".join(created)
        + "\n"
    )
    out = HEADER + "\n" + main_before_seed.rstrip("\n") + "\n\n" + more_tables + seed.rstrip("\n") + "\n"
    out = re.sub(r"\n{3,}", "\n\n", out)                           # no runs of blank lines left by the removed blocks
    # ALTER TABLE ... ADD CONSTRAINT (the foreign keys of section 4) and the seed DO block are creation statements and stay;
    # what must not be here is anything that changes an existing table.
    code = "\n".join(l for l in out.splitlines() if not l.lstrip().startswith("--"))
    for forbidden in (r"(?i)\bADD\s+COLUMN\b", r"(?i)\bDROP\s+(TABLE|COLUMN|CONSTRAINT|INDEX)\b", r"(?i)\bRENAME\s+TO\b",
                      r"(?i)\bALTER\s+(INDEX|SEQUENCE|COLUMN)\b"):
        if re.search(forbidden, code):
            raise SystemExit(f"the fresh file would contain a statement that changes an existing database ({forbidden})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only verify that clockyn_ddl_fresh.sql is up to date")
    args = ap.parse_args(argv)
    fresh = build(LATEST.read_text(encoding="utf-8"))
    if args.check:
        current = FRESH.read_text(encoding="utf-8").replace("\r\n", "\n") if FRESH.exists() else ""
        if current != fresh:
            print("clockyn_ddl_fresh.sql is out of date: run  python scripts/build_fresh_ddl.py", file=sys.stderr)
            return 1
        print("clockyn_ddl_fresh.sql is up to date")
        return 0
    FRESH.write_text(fresh, encoding="utf-8", newline="\n")
    print(f"wrote {FRESH.name} ({len(fresh.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
