#!/usr/bin/env python3
"""Export the live FAQ (table support_faq, status = 1) to rows.json so the knowledge base can be refreshed after support edited
FAQs in the console FAQ Hub. The Markdown is NOT written here: there is one renderer, scripts/faq-to-kb.js.

    python scripts/export-faq-from-db.py [--out rows.json]
    node scripts/faq-to-kb.js --rows rows.json

Connection (environment, same names as the Clockyn backend): DB_HOST, DB_PORT (default 5432), DB_NAME, DB_USER, DB_PASSWORD,
DB_SCHEMA (default clockyn_dev). Read-only: it runs one SELECT. Never put the password on the command line.

rows.json (the documented intermediate):
  {"generated": "YYYY-MM-DD", "source": "db",
   "rows": [{"id": 1, "bucket": "Set up & Account", "category": "Getting Started", "question": "...", "answer": "...",
             "sort_order": 10, "status": 1}]}
"generated" = the newest updated_at / created_at date of the exported rows (so the same data always gives the same files).
"""
import argparse
import json
import os
import re
import sys

SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_payload(db_rows, fallback_date):
    """db_rows: iterable of (id, faq_bucket, category, question, answer, sort_order, status, stamp) -> the rows.json dict."""
    rows = []
    newest = None
    for (rid, bucket, category, question, answer, sort_order, status, stamp) in db_rows:
        rows.append({
            "id": rid, "bucket": bucket, "category": category, "question": question, "answer": answer,
            "sort_order": int(sort_order or 0), "status": int(status if status is not None else 1),
        })
        day = stamp.date().isoformat() if hasattr(stamp, "date") else (str(stamp)[:10] if stamp else None)
        if day and (newest is None or day > newest):
            newest = day
    return {"generated": newest or fallback_date, "source": "db", "rows": rows}


def fetch_rows(conn, schema):
    if not SCHEMA_RE.match(schema):
        raise SystemExit("DB_SCHEMA must be a plain identifier")
    sql = (
        'SELECT id, faq_bucket, category, question, answer, sort_order, status, COALESCE(updated_at, created_at) '
        'FROM "%s".support_faq WHERE status = 1 ORDER BY sort_order, id' % schema
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="rows.json", help="where to write rows.json (default ./rows.json)")
    args = ap.parse_args(argv)
    try:
        import psycopg2  # imported here so the helpers above can be tested without the driver
    except ImportError:
        print("psycopg2 is required: pip install psycopg2-binary", file=sys.stderr)
        return 2
    missing = [k for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if not os.environ.get(k)]
    if missing:
        print("missing environment variables: " + ", ".join(missing), file=sys.stderr)
        return 2
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")), dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    )
    conn.set_session(readonly=True)
    try:
        import datetime
        payload = build_payload(fetch_rows(conn, os.environ.get("DB_SCHEMA", "clockyn_dev")), datetime.date.today().isoformat())
    finally:
        conn.close()
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote %s: %d questions (generated %s). Next: node scripts/faq-to-kb.js --rows %s" % (args.out, len(payload["rows"]), payload["generated"], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
