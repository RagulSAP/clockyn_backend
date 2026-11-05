#!/usr/bin/env python3
"""Schema consistency check: punchapp_ddl_latest.sql  vs  migrations/NNN_*.sql  (no database needed).

Why: `python migrate.py --all` on an empty database applies the DDL file and then RECORDS every migration as applied
without running it. So every object a migration creates or changes must already be in the DDL, with a compatible
definition, or a fresh database silently differs from a migrated one.

What it does (parser: the real PostgreSQL parser, pglast)
  * builds a catalog from the DDL (tables, columns, constraints, indexes, sequences, enum values, seeded tables);
  * extracts the effects of every migration: CREATE TABLE, ALTER TABLE ADD COLUMN / ADD CONSTRAINT /
    ALTER COLUMN TYPE / SET DEFAULT / SET NOT NULL, CREATE [UNIQUE] INDEX, CREATE SEQUENCE, CREATE TYPE /
    ALTER TYPE ADD VALUE, INSERT seeds, DROP INDEX / DROP CONSTRAINT (the dropped object must NOT be in the DDL);
  * statements inside DO blocks are extracted when they are plain ALTER/CREATE/DROP statements; blocks with nothing
    extractable (pure checks / RAISE) are listed as "not analysable";
  * compares each effect with the DDL: DRIFT findings (missing object, other type, nullability, default, index keys,
    uniqueness, partial predicate, constraint definition);
  * migration sanity: parses, no BEGIN/COMMIT/CONCURRENTLY, idempotent forms (IF NOT EXISTS, IF EXISTS, ON CONFLICT,
    guarded ADD CONSTRAINT), no data deletion, a unique index/constraint over a pre-existing table has a pre-check,
    duplicate numbers, a migration must not use a table a LATER migration creates;
  * DDL sanity: duplicate table / index / constraint names.

Usage (backend root):  python scripts/check_schema_consistency.py [--ddl FILE] [--migrations DIR] [-v]
Exit code 1 when there is any DRIFT or ERROR finding. WARN findings are informational.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DDL = BE_ROOT / "punchapp_ddl_latest.sql"
DEFAULT_MIGRATIONS = BE_ROOT / "migrations"
_FALLBACK_PGLAST = (
    r"C:\Users\RSITHU~1\AppData\Local\Temp\claude\c--POC-pa-frontend\90c115e7-e99e-4778-ad6c-7055cb1620cc"
    r"\scratchpad\pylibs"
)


def _import_pglast():
    try:
        import pglast  # noqa: F401
        return
    except ImportError:
        pass
    for p in (os.environ.get("PGLAST_LIBS"), _FALLBACK_PGLAST):
        if p and os.path.isdir(p):
            sys.path.insert(0, p)
            try:
                import pglast  # noqa: F401
                return
            except ImportError:
                continue
    raise SystemExit("pglast is required: pip install pglast (or set PGLAST_LIBS to its folder)")


_import_pglast()
import pglast  # noqa: E402
from pglast import ast as A  # noqa: E402
from pglast import enums as E  # noqa: E402
from pglast.stream import RawStream  # noqa: E402

SCHEMA_QUALIFIER = "punchapp_prod"
MIGRATION_RE = re.compile(r"^(\d{3,})_[A-Za-z0-9_]+\.sql$")


# ------------------------------------------------------------------------------------------------ findings
@dataclass
class Finding:
    level: str          # DRIFT | ERROR | WARN
    where: str          # file name
    message: str

    def __str__(self) -> str:
        return f"{self.level:5} {self.where}: {self.message}"


# ------------------------------------------------------------------------------------------------ normalisation
_CAST_CONST = re.compile(r"CAST\(('(?:[^']|'')*'|-?\d+(?:\.\d+)?|true|false) AS [a-z_ ]+(?:\([^)]*\))?\)", re.I)
_NOW = re.compile(r"\b(current_timestamp|transaction_timestamp\(\)|now\(\))", re.I)


def norm_sql(text: str) -> str:
    """Canonical text of an expression: no schema qualifier, constant casts unwrapped, now() spellings unified."""
    t = text.replace(f"{SCHEMA_QUALIFIER}.", "")
    for _ in range(3):
        t = _CAST_CONST.sub(r"\1", t)
    t = _NOW.sub("NOW", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    t = re.sub(r"^\((.*)\)$", r"\1", t)
    return t


def expr_text(node) -> str:
    return norm_sql(RawStream()(node)) if node is not None else ""


_TYPE_ALIAS = {"int": "int4", "integer": "int4", "smallint": "int2", "bigint": "int8", "boolean": "bool",
               "character varying": "varchar", "character": "bpchar", "double precision": "float8", "real": "float4",
               "decimal": "numeric", "timestamp without time zone": "timestamp",
               "timestamp with time zone": "timestamptz", "serial": "int4", "bigserial": "int8"}


def type_text(tn) -> str:
    names = [n.sval for n in tn.names]
    base = names[-1].lower()
    base = _TYPE_ALIAS.get(base, base)
    mods = []
    for m in tn.typmods or ():
        c = getattr(m, "val", None)
        if c is not None and hasattr(c, "ival"):
            mods.append(str(c.ival))
        else:
            mods.append("?")
    s = base + (f"({','.join(mods)})" if mods else "")
    s += "[]" * len(tn.arrayBounds or ())
    return s


@dataclass
class Col:
    name: str
    type: str
    notnull: bool = False
    default: str = ""
    identity: bool = False


@dataclass
class Constraint:
    name: str
    kind: str           # p | u | c | f
    cols: tuple = ()
    expr: str = ""      # check expression or "reftable(refcols)"


@dataclass
class Index:
    name: str
    table: str
    unique: bool
    keys: tuple
    where: str = ""
    method: str = "btree"


@dataclass
class Table:
    name: str
    cols: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)


def _rel(rv) -> str:
    return rv.relname


def _col_from_def(cd) -> tuple[Col, list[Constraint]]:
    col = Col(cd.colname, type_text(cd.typeName))
    extra: list[Constraint] = []
    for c in cd.constraints or ():
        t = c.contype
        if t == E.ConstrType.CONSTR_NOTNULL:
            col.notnull = True
        elif t == E.ConstrType.CONSTR_NULL:
            col.notnull = False
        elif t == E.ConstrType.CONSTR_DEFAULT:
            col.default = expr_text(c.raw_expr)
        elif t == E.ConstrType.CONSTR_IDENTITY:
            col.identity = True
            col.notnull = True
        elif t == E.ConstrType.CONSTR_PRIMARY:
            col.notnull = True
            extra.append(Constraint(c.conname or "", "p", (cd.colname,)))
        elif t == E.ConstrType.CONSTR_UNIQUE:
            extra.append(Constraint(c.conname or "", "u", (cd.colname,)))
        elif t == E.ConstrType.CONSTR_CHECK:
            extra.append(Constraint(c.conname or "", "c", (), expr_text(c.raw_expr)))
        elif t == E.ConstrType.CONSTR_FOREIGN:
            extra.append(_fk(c, (cd.colname,)))
    return col, extra


def _fk(c, cols) -> Constraint:
    ref_cols = tuple(x.sval for x in (c.pk_attrs or ()))
    return Constraint(c.conname or "", "f", tuple(cols), f"{c.pktable.relname}({','.join(ref_cols)})")


def _table_constraint(c) -> Constraint | None:
    t = c.contype
    cols = tuple(x.sval for x in (c.keys or ()))
    if t == E.ConstrType.CONSTR_PRIMARY:
        return Constraint(c.conname or "", "p", cols)
    if t == E.ConstrType.CONSTR_UNIQUE:
        return Constraint(c.conname or "", "u", cols)
    if t == E.ConstrType.CONSTR_CHECK:
        return Constraint(c.conname or "", "c", (), expr_text(c.raw_expr))
    if t == E.ConstrType.CONSTR_FOREIGN:
        return _fk(c, tuple(x.sval for x in (c.fk_attrs or ())))
    return None


def _add_constraint(tbl: Table, con: Constraint) -> None:
    if not con.name:
        stem = {"p": "pkey", "u": "key", "c": "check", "f": "fkey"}[con.kind]
        con.name = f"{tbl.name}_{'_'.join(con.cols) if con.cols else 'expr'}_{stem}"
    tbl.constraints[con.name] = con
    if con.kind == "p":
        for cn in con.cols:
            if cn in tbl.cols:
                tbl.cols[cn].notnull = True


def table_from_create(st) -> Table:
    tbl = Table(_rel(st.relation))
    pending: list[Constraint] = []
    for el in st.tableElts or ():
        if isinstance(el, A.ColumnDef):
            col, extra = _col_from_def(el)
            tbl.cols[col.name] = col
            pending.extend(extra)
        elif isinstance(el, A.Constraint):
            con = _table_constraint(el)
            if con:
                pending.append(con)
    for con in pending:
        _add_constraint(tbl, con)
    return tbl


def index_from_stmt(st) -> Index:
    keys = []
    for p in st.indexParams or ():
        keys.append(p.name.lower() if p.name else f"({expr_text(p.expr)})")
    return Index(st.idxname, _rel(st.relation), bool(st.unique), tuple(keys),
                 expr_text(st.whereClause), (st.accessMethod or "btree").lower())


# ------------------------------------------------------------------------------------------------ catalog (DDL)
@dataclass
class Catalog:
    tables: dict = field(default_factory=dict)
    indexes: dict = field(default_factory=dict)
    sequences: set = field(default_factory=set)
    enums: dict = field(default_factory=dict)       # name -> list of values
    seeded: set = field(default_factory=set)        # tables with an INSERT
    errors: list = field(default_factory=list)
    names: set = field(default_factory=set)         # relation namespace: index names + pk/unique constraint names

    def apply(self, st, where: str) -> None:
        if isinstance(st, A.CreateStmt):
            t = table_from_create(st)
            if t.name in self.tables and not st.if_not_exists:
                self.errors.append(Finding("ERROR", where, f"table {t.name} created twice"))
            self.tables.setdefault(t.name, t)
            for con in t.constraints.values():
                if con.kind in ("p", "u"):
                    self._claim(con.name, where)
        elif isinstance(st, A.IndexStmt):
            ix = index_from_stmt(st)
            self._claim(ix.name, where, st.if_not_exists)
            self.indexes.setdefault(ix.name, ix)
        elif isinstance(st, A.AlterTableStmt):
            for cmd in st.cmds:
                self._alter(_rel(st.relation), cmd, where)
        elif isinstance(st, A.CreateSeqStmt):
            self.sequences.add(_rel(st.sequence))
        elif isinstance(st, A.CreateEnumStmt):
            self.enums[st.typeName[-1].sval] = [v.sval for v in st.vals]
        elif isinstance(st, A.InsertStmt):
            self.seeded.add(_rel(st.relation))

    def _claim(self, name: str, where: str, if_not_exists: bool = False) -> None:
        if name in self.names and not if_not_exists:
            self.errors.append(Finding("ERROR", where, f"index/constraint name {name} used twice"))
        self.names.add(name)

    def _alter(self, table: str, cmd, where: str) -> None:
        t = self.tables.get(table)
        if t is None:
            self.errors.append(Finding("ERROR", where, f"ALTER TABLE on unknown table {table}"))
            return
        if cmd.subtype == E.AlterTableType.AT_AddConstraint:
            con = _table_constraint(cmd.def_)
            if con:
                _add_constraint(t, con)
                if con.kind in ("p", "u"):
                    self._claim(con.name, where)
        elif cmd.subtype == E.AlterTableType.AT_AddColumn:
            col, extra = _col_from_def(cmd.def_)
            t.cols[col.name] = col
            for con in extra:
                _add_constraint(t, con)


def load_catalog(ddl_sql: str, where: str = "punchapp_ddl_latest.sql") -> Catalog:
    cat = Catalog()
    for raw in pglast.parse_sql(ddl_sql):
        cat.apply(raw.stmt, where)
    return cat


# ------------------------------------------------------------------------------------------------ migration effects
_DO_FRAGMENT = re.compile(
    r"(?:^|\bTHEN\s+|\bELSE\s+|\bBEGIN\s+)\s*((?:ALTER TABLE|CREATE (?:UNIQUE )?INDEX|CREATE TABLE|CREATE SEQUENCE|"
    r"DROP INDEX|INSERT INTO)\b.*)$", re.I | re.S)


def _strip_sql_comments(s: str) -> str:
    return re.sub(r"--[^\n]*", "", s)


def _split_top_level(body: str) -> list[str]:
    out, cur, q = [], [], False
    for ch in body:
        if ch == "'":
            q = not q
        if ch == ";" and not q:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def do_block_statements(do_stmt) -> tuple[list, list[str]]:
    """Plain ALTER/CREATE/DROP/INSERT statements found in a DO block: (parsed statements, unparsable fragments)."""
    body = ""
    for arg in do_stmt.args or ():
        if arg.defname == "as":
            body = arg.arg.sval
    found, bad = [], []
    for frag in _split_top_level(_strip_sql_comments(body)):
        m = _DO_FRAGMENT.search(frag.strip())
        if not m:
            continue
        sql = re.sub(r"\s+", " ", m.group(1)).strip()
        try:
            found.append(pglast.parse_sql(sql)[0].stmt)
        except pglast.parser.ParseError:
            bad.append(sql[:80])
    return found, bad


@dataclass
class MigrationInfo:
    name: str
    number: int
    text: str
    stmts: list = field(default_factory=list)             # (stmt, in_do)
    analysable_do: int = 0
    unanalysable_do: int = 0
    unparsable_fragments: list = field(default_factory=list)
    parse_error: str = ""
    created_tables: set = field(default_factory=set)
    findings: list = field(default_factory=list)


def load_migrations(mig_dir: Path) -> list[MigrationInfo]:
    infos: list[MigrationInfo] = []
    seen: dict[int, str] = {}
    for p in sorted(mig_dir.glob("*.sql")):
        m = MIGRATION_RE.match(p.name)
        if not m:
            continue
        num = int(m.group(1))
        info = MigrationInfo(p.name, num, p.read_text(encoding="utf-8"))
        if num in seen:
            info.findings.append(Finding("ERROR", p.name, f"duplicate migration number {num} (also {seen[num]})"))
        seen[num] = p.name
        try:
            parsed = pglast.parse_sql(info.text)
        except pglast.parser.ParseError as exc:
            info.parse_error = str(exc)
            info.findings.append(Finding("ERROR", p.name, f"does not parse: {exc}"))
            infos.append(info)
            continue
        for raw in parsed:
            st = raw.stmt
            if isinstance(st, A.DoStmt):
                inner, bad = do_block_statements(st)
                info.unparsable_fragments += bad
                if inner:
                    info.analysable_do += 1
                else:
                    info.unanalysable_do += 1
                for s in inner:
                    info.stmts.append((s, True))
            else:
                info.stmts.append((st, False))
        for s, _ in info.stmts:
            if isinstance(s, A.CreateStmt):
                info.created_tables.add(_rel(s.relation))
        infos.append(info)
    return sorted(infos, key=lambda i: i.number)


# ------------------------------------------------------------------------------------------------ comparison
def _col_diff(mig: Col, ddl: Col) -> list[str]:
    out = []
    if mig.type != ddl.type:
        out.append(f"type {mig.type} (migration) vs {ddl.type} (DDL)")
    if mig.notnull != ddl.notnull:
        out.append(f"NOT NULL={mig.notnull} (migration) vs {ddl.notnull} (DDL)")
    if mig.default != ddl.default:
        out.append(f"DEFAULT {mig.default or '-'} (migration) vs {ddl.default or '-'} (DDL)")
    if mig.identity != ddl.identity:
        out.append(f"identity={mig.identity} (migration) vs {ddl.identity} (DDL)")
    return out


def _find_constraint(tbl: Table, con: Constraint, explicit_name: bool):
    hit = tbl.constraints.get(con.name) if explicit_name else None
    if hit is not None:
        return hit, True
    for c in tbl.constraints.values():
        if c.kind == con.kind and c.cols == con.cols and c.expr == con.expr:
            return c, False
    return None, False


def check_constraint(where, tname, con: Constraint, explicit_name, cat: Catalog, out: list) -> None:
    tbl = cat.tables.get(tname)
    if tbl is None:
        out.append(Finding("DRIFT", where, f"table {tname} (constraint {con.name}) is not in the DDL"))
        return
    hit, by_name = _find_constraint(tbl, con, explicit_name)
    if hit is None:
        out.append(Finding("DRIFT", where, f"constraint {con.name} on {tname} {con.kind}{con.cols} {con.expr} "
                                           f"is not in the DDL"))
    elif explicit_name and not by_name:
        out.append(Finding("DRIFT", where, f"constraint {con.name} on {tname} exists in the DDL under another name "
                                           f"({hit.name})"))
    elif explicit_name and (hit.kind, hit.cols, hit.expr) != (con.kind, con.cols, con.expr):
        out.append(Finding("DRIFT", where, f"constraint {con.name} on {tname} differs: migration "
                                           f"{con.kind}{con.cols} {con.expr} vs DDL {hit.kind}{hit.cols} {hit.expr}"))


def check_table(where, mt: Table, cat: Catalog, out: list) -> None:
    dt = cat.tables.get(mt.name)
    if dt is None:
        out.append(Finding("DRIFT", where, f"table {mt.name} is not in the DDL"))
        return
    for c in mt.cols.values():
        d = dt.cols.get(c.name)
        if d is None:
            out.append(Finding("DRIFT", where, f"column {mt.name}.{c.name} is not in the DDL"))
            continue
        for diff in _col_diff(c, d):
            out.append(Finding("DRIFT", where, f"column {mt.name}.{c.name}: {diff}"))
    for con in mt.constraints.values():
        check_constraint(where, mt.name, con, True, cat, out)


def check_index(where, ix: Index, cat: Catalog, out: list) -> None:
    d = cat.indexes.get(ix.name)
    if d is None:
        # a UNIQUE constraint in the DDL is an equally valid backing for a unique index of the same columns
        t = cat.tables.get(ix.table)
        if t and ix.unique and not ix.where:
            for c in t.constraints.values():
                if c.kind in ("u", "p") and c.cols == ix.keys:
                    return
        out.append(Finding("DRIFT", where, f"index {ix.name} on {ix.table}{ix.keys} is not in the DDL"))
        return
    for label, a, b in (("table", ix.table, d.table), ("UNIQUE", ix.unique, d.unique), ("keys", ix.keys, d.keys),
                        ("WHERE", ix.where, d.where), ("method", ix.method, d.method)):
        if a != b:
            out.append(Finding("DRIFT", where, f"index {ix.name}: {label} {a!r} (migration) vs {b!r} (DDL)"))
    t = cat.tables.get(ix.table)
    if t is not None:
        for k in ix.keys:
            if not k.startswith("(") and k not in t.cols:
                out.append(Finding("DRIFT", where, f"index {ix.name} uses column {ix.table}.{k} that is not in the DDL"))


def _drop_names(st) -> list[tuple[str, str]]:
    out = []
    if isinstance(st, A.DropStmt):
        for obj in st.objects or ():
            parts = obj if isinstance(obj, tuple) else getattr(obj, "items", obj)
            names = [p.sval for p in parts] if isinstance(parts, tuple) else []
            if names:
                kind = "index" if st.removeType == E.ObjectType.OBJECT_INDEX else (
                    "table" if st.removeType == E.ObjectType.OBJECT_TABLE else "other")
                out.append((kind, names[-1]))
    return out


def analyse_migration(info: MigrationInfo, cat: Catalog, created_by: dict, all_infos: list) -> list[Finding]:
    out = list(info.findings)
    w = info.name
    if info.parse_error:
        return out
    # --- text level rules
    if re.search(r"\bCONCURRENTLY\b", _strip_sql_comments(info.text), re.I):
        out.append(Finding("ERROR", w, "CREATE INDEX CONCURRENTLY cannot run inside the runner's transaction"))
    precheck = bool(re.search(r"having\s+count", info.text, re.I))
    for frag in info.unparsable_fragments:
        out.append(Finding("WARN", w, f"DO block fragment not analysable: {frag}"))
    # --- statement level
    for st, in_do in info.stmts:
        if isinstance(st, A.TransactionStmt):
            out.append(Finding("ERROR", w, "BEGIN/COMMIT inside a migration (the runner wraps the file)"))
        elif isinstance(st, A.CreateStmt):
            if not st.if_not_exists:
                out.append(Finding("ERROR", w, f"CREATE TABLE {_rel(st.relation)} without IF NOT EXISTS (not re-runnable)"))
            check_table(w, table_from_create(st), cat, out)
        elif isinstance(st, A.IndexStmt):
            if not st.if_not_exists:
                out.append(Finding("ERROR", w, f"CREATE INDEX {st.idxname} without IF NOT EXISTS (not re-runnable)"))
            ix = index_from_stmt(st)
            check_index(w, ix, cat, out)
            _order_check(w, info, ix.table, created_by, out)
            if ix.unique and ix.table not in info.created_tables and not precheck:
                out.append(Finding("ERROR", w, f"unique index {ix.name} over existing table {ix.table} without a "
                                               f"duplicate pre-check (HAVING count(*) > 1 query) in the file"))
        elif isinstance(st, A.AlterTableStmt):
            tname = _rel(st.relation)
            _order_check(w, info, tname, created_by, out)
            for cmd in st.cmds:
                _alter_effect(w, info, tname, cmd, in_do, cat, precheck, out)
        elif isinstance(st, A.CreateSeqStmt):
            if not st.if_not_exists:
                out.append(Finding("ERROR", w, f"CREATE SEQUENCE {_rel(st.sequence)} without IF NOT EXISTS"))
            if _rel(st.sequence) not in cat.sequences:
                out.append(Finding("DRIFT", w, f"sequence {_rel(st.sequence)} is not in the DDL"))
        elif isinstance(st, A.CreateEnumStmt):
            name = st.typeName[-1].sval
            vals = [v.sval for v in st.vals]
            if cat.enums.get(name) != vals:
                out.append(Finding("DRIFT", w, f"enum {name} {vals} vs DDL {cat.enums.get(name)}"))
        elif isinstance(st, A.AlterEnumStmt):
            name = st.typeName[-1].sval
            if st.newVal and st.newVal not in cat.enums.get(name, []):
                out.append(Finding("DRIFT", w, f"enum value {name}.{st.newVal} is not in the DDL"))
            if not st.skipIfNewValExists:
                out.append(Finding("ERROR", w, f"ALTER TYPE {name} ADD VALUE without IF NOT EXISTS"))
        elif isinstance(st, A.InsertStmt):
            tname = _rel(st.relation)
            _order_check(w, info, tname, created_by, out)
            if not st.onConflictClause:
                out.append(Finding("ERROR", w, f"INSERT INTO {tname} without ON CONFLICT (not re-runnable)"))
            dt = cat.tables.get(tname)
            if dt is None:
                out.append(Finding("DRIFT", w, f"INSERT target {tname} is not in the DDL"))
            else:
                for rc in st.cols or ():
                    if rc.name not in dt.cols:
                        out.append(Finding("DRIFT", w, f"INSERT column {tname}.{rc.name} is not in the DDL"))
                sel = st.selectStmt
                literal = isinstance(sel, A.SelectStmt) and bool(sel.valuesLists)
                if literal and tname not in cat.seeded:
                    out.append(Finding("DRIFT", w, f"seed rows for {tname} are not in the DDL seed section"))
        elif isinstance(st, A.UpdateStmt):
            if st.whereClause is None:
                out.append(Finding("WARN", w, f"UPDATE {_rel(st.relation)} without WHERE (touches every row)"))
            _order_check(w, info, _rel(st.relation), created_by, out)
        elif isinstance(st, (A.DeleteStmt, A.TruncateStmt)):
            out.append(Finding("ERROR", w, "migrations must never delete data"))
        elif isinstance(st, A.DropStmt):
            for kind, name in _drop_names(st):
                if kind == "table":
                    out.append(Finding("ERROR", w, f"DROP TABLE {name}: migrations must never delete data"))
                if not st.missing_ok:
                    out.append(Finding("ERROR", w, f"DROP {kind} {name} without IF EXISTS (not re-runnable)"))
                if kind == "index" and name in cat.indexes:
                    out.append(Finding("DRIFT", w, f"index {name} is dropped by the migration but still in the DDL"))
                    continue
                if kind == "index" and any(c.name == name for t in cat.tables.values()
                                           for c in t.constraints.values()):
                    out.append(Finding("DRIFT", w, f"{name} is dropped by the migration but a constraint of that "
                                                   f"name is in the DDL"))
    return out


def _order_check(w: str, info: MigrationInfo, table: str, created_by: dict, out: list) -> None:
    n = created_by.get(table)
    if n is not None and n > info.number:
        out.append(Finding("ERROR", w, f"uses table {table} that is created later by migration {n:03d}"))


def _alter_effect(w, info, tname, cmd, in_do, cat, precheck, out) -> None:
    T = E.AlterTableType
    dt = cat.tables.get(tname)
    if dt is None:
        out.append(Finding("DRIFT", w, f"table {tname} is not in the DDL"))
        return
    if cmd.subtype == T.AT_AddColumn:
        if not cmd.missing_ok:
            out.append(Finding("ERROR", w, f"ADD COLUMN {tname}.{cmd.def_.colname} without IF NOT EXISTS"))
        col, extra = _col_from_def(cmd.def_)
        d = dt.cols.get(col.name)
        if d is None:
            out.append(Finding("DRIFT", w, f"column {tname}.{col.name} is not in the DDL"))
        else:
            for diff in _col_diff(col, d):
                out.append(Finding("DRIFT", w, f"column {tname}.{col.name}: {diff}"))
        for con in extra:
            if not con.name:
                con.name = f"{tname}_{'_'.join(con.cols)}_key"
            check_constraint(w, tname, con, False, cat, out)
    elif cmd.subtype == T.AT_AddConstraint:
        con = _table_constraint(cmd.def_)
        if con is None:
            return
        if not in_do:
            out.append(Finding("ERROR", w, f"ADD CONSTRAINT {con.name} outside a guarded DO block (not re-runnable)"))
        if con.kind in ("u", "p") and tname not in info.created_tables and not precheck:
            out.append(Finding("ERROR", w, f"unique constraint {con.name} over existing table {tname} without a "
                                           f"duplicate pre-check in the file"))
        check_constraint(w, tname, con, bool(con.name), cat, out)
    elif cmd.subtype == T.AT_AlterColumnType:
        col = dt.cols.get(cmd.name)
        new_t = type_text(cmd.def_.typeName)
        if col is None:
            out.append(Finding("DRIFT", w, f"ALTER COLUMN TYPE: column {tname}.{cmd.name} is not in the DDL"))
        elif col.type != new_t:
            out.append(Finding("DRIFT", w, f"column {tname}.{cmd.name}: migration changes type to {new_t}, DDL has {col.type}"))
        if not in_do:
            out.append(Finding("WARN", w, f"ALTER COLUMN {tname}.{cmd.name} TYPE outside a guarded DO block"
                                          f" (rewrites the table on every run)"))
    elif cmd.subtype == T.AT_ColumnDefault:
        col = dt.cols.get(cmd.name)
        new_d = expr_text(cmd.def_) if cmd.def_ is not None else ""
        if col is None:
            out.append(Finding("DRIFT", w, f"SET DEFAULT: column {tname}.{cmd.name} is not in the DDL"))
        elif col.default != new_d:
            out.append(Finding("DRIFT", w, f"column {tname}.{cmd.name}: migration default {new_d or '-'} vs DDL "
                                           f"{col.default or '-'}"))
    elif cmd.subtype in (T.AT_SetNotNull, T.AT_DropNotNull):
        col = dt.cols.get(cmd.name)
        want = cmd.subtype == T.AT_SetNotNull
        if col is None or col.notnull != want:
            out.append(Finding("DRIFT", w, f"column {tname}.{cmd.name}: NOT NULL should be {want} in the DDL"))
    elif cmd.subtype == T.AT_DropConstraint:
        if not cmd.missing_ok and not in_do:
            out.append(Finding("ERROR", w, f"DROP CONSTRAINT {cmd.name} without IF EXISTS or a guarding DO block"))
        if cmd.name in dt.constraints:
            out.append(Finding("DRIFT", w, f"constraint {cmd.name} is dropped by the migration but still in the DDL"))
    elif cmd.subtype == T.AT_DropColumn:
        out.append(Finding("ERROR", w, f"DROP COLUMN {tname}.{cmd.name}: migrations must never delete data"))


# ------------------------------------------------------------------------------------------------ driver
def run(ddl_path: Path, mig_dir: Path) -> tuple[list[Finding], list[MigrationInfo], Catalog]:
    cat = load_catalog(ddl_path.read_text(encoding="utf-8"), ddl_path.name)
    infos = load_migrations(mig_dir)
    created_by: dict[str, int] = {}
    for i in infos:
        for t in i.created_tables:
            created_by.setdefault(t, i.number)
    findings: list[Finding] = list(cat.errors)
    for i in infos:
        findings += analyse_migration(i, cat, created_by, infos)
    return findings, infos, cat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DDL vs migrations consistency check (pglast, no database)")
    ap.add_argument("--ddl", default=str(DEFAULT_DDL))
    ap.add_argument("--migrations", default=str(DEFAULT_MIGRATIONS))
    ap.add_argument("-v", "--verbose", action="store_true", help="print the per-migration summary")
    args = ap.parse_args(argv)
    findings, infos, cat = run(Path(args.ddl), Path(args.migrations))
    if args.verbose:
        for i in infos:
            n = len(i.stmts)
            print(f"  {i.name}: {n} analysable statements, DO blocks analysed={i.analysable_do} "
                  f"not-analysable={i.unanalysable_do}")
    for f in findings:
        print(f)
    bad = [f for f in findings if f.level in ("DRIFT", "ERROR")]
    print(f"\nDDL: {len(cat.tables)} tables, {len(cat.indexes)} indexes, {len(cat.sequences)} sequences, "
          f"{len(cat.enums)} enums; migrations: {len(infos)}; findings: {len(findings)} ({len(bad)} blocking)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
