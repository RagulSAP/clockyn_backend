# Database Migrations

Numbered, idempotent SQL files applied in order by `migrate.py`. The full schema for a new database is
`clockyn_ddl_latest.sql` (the baseline); every migration file is also reflected there.

## Commands (run from the backend root, DB_* variables in `.env`, `DB_SCHEMA` = target schema)

```bash
python migrate.py --status             # applied / pending per file
python migrate.py --all --dry-run      # what --all would do (reads the database, writes nothing)
python migrate.py --all                # apply every pending file, in numeric order
python migrate.py --all --schema other # override DB_SCHEMA
python migrate.py <schema> <file.sql>  # legacy: run one file on one schema (not recorded)
```

`PGSSLMODE` (and the other libpq variables) work as usual. `RUN_MIGRATIONS=1` makes `app.py` run the same function at start-up;
a failure is logged and never stops the app. Two processes starting together are serialised by a Postgres advisory lock.

## What `--all` does

Applied versions live in `<schema>.schema_migrations(version text primary key, applied_at timestamptz)` (created if absent;
a version is the file name without `.sql`, e.g. `024_otp_send_log`).

| State of the schema | Action |
|---|---|
| no `users` table (empty / new) | apply `clockyn_ddl_latest.sql` (qualifier `clockyn_dev` rewritten to the target schema), then record **every** migration file as applied without running it |
| `users` exists, `schema_migrations` empty (legacy database) | run every migration file in order (all are idempotent) and record each |
| otherwise | run only the files not yet recorded |
| versions recorded but no `users` table | stop with an error (inconsistent schema) |

Each file runs in its own transaction together with its `schema_migrations` row. A failure rolls back that file and stops the run;
earlier files stay applied. Fix and re-run: finished files are skipped.

## Adding a migration

1. Create `NNN_short_description.sql` with the next free number (three or more digits, no gaps required, **no duplicate numbers**; the runner refuses duplicates and any `.sql` file in this folder that does not match `NNN_name.sql`).
2. Idempotent (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, ...), unqualified names (the schema comes from the search path), never deletes data.
3. No `BEGIN` / `COMMIT` and no `CREATE INDEX CONCURRENTLY` (the runner already wraps the file in a transaction).
4. A constraint or unique index over existing data: put the pre-check query in a comment at the top (see `025_org_holidays_unique_scope.sql`).
5. Put the same objects into `clockyn_ddl_latest.sql` (schema-qualified), so a new database and a migrated one end up equal.
6. Test on a copy first: `python migrate.py --all --dry-run`, then `--all`, then `--status`.

## Deploying to another schema

Set `DB_SCHEMA=<schema>` in the environment and run `python migrate.py --all`; the schema is created when missing.
