#!/usr/bin/env python3
"""Live SQL check: validates EVERY SQL statement of the backend against a real PostgreSQL with its own parser/planner.

Nothing from the code is ever executed. Each statement is sent as `PREPARE s(..) AS <statement>` (parse, analyse,
rewrite; no execution), then planned with `EXPLAIN (COSTS OFF) EXECUTE s(NULL, ...)` (no ANALYZE: plans, never runs;
catches planner-stage errors such as an ON CONFLICT target without a matching unique index), then discarded.
Safety rules (the server also holds real data in other schemas):
  * the connection comes from tests/pg_scratch.py (settings from the backend `.env`; never printed); set PGSSLMODE=require;
  * everything happens inside ONE scratch schema `clockyn_dev_test_<hex>` created by pg_scratch and dropped in `finally`;
  * `search_path` is the scratch schema only (no `public`), the validation session is read-only, every statement is
    first checked with the PostgreSQL parser to be exactly ONE select/insert/update/delete before it is sent;
  * migration files (migrations/*.sql, the only DDL applied) run on a separate connection inside a transaction that is
    always rolled back, after text guards (no schema/database/role/extension statements, no foreign schema names).

Extraction reuses scripts/sql_static_check.py (ast walk, f-string / `+=` / `.format` resolution) with two additions:
`.format(...)` templates whose arguments are literals are formatted for real, and SQL constants (module level, dict
values, arguments to helper functions) that are not passed to execute() directly are validated too ("constant"/"orphan").

Result classes
  OK          PREPARE accepted (parameters inferred, or declared by the retry loop after a type mismatch)
  UNTYPED     PostgreSQL could not infer a parameter type (psycopg2 sends literals, so this is noise); the retry
              declared it as text/other and the statement then passed. Listed in scripts/sql_live_baseline.json
  REAL        a genuine error (syntax, relation/column/function missing, ambiguity, GROUP BY, type mismatch that
              persists after up to 6 retries, ON CONFLICT without matching index ...)
  APPROX      statement with unresolved f-string parts: no rendering could be prepared (reported, never fails the run)
  UTILITY     not PREPARE-able (SET, LOCK, DDL ...): syntax checked by the parser only

Usage:  PGSSLMODE=require python scripts/sql_live_check.py [--json] [--paths P ...] [--no-migrations]
                                                         [--write-baseline] [--all]
Exit code 1 when there are REAL errors in non-approximate statements (or a migration fails) that are not accepted
in the baseline file.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_ROOT / "scripts"))
sys.path.insert(0, str(BE_ROOT / "tests"))

import sql_static_check as S  # noqa: E402  (also makes pglast importable)
import pglast  # noqa: E402
from pglast import ast as A  # noqa: E402

BASELINE_FILE = BE_ROOT / "scripts" / "sql_live_baseline.json"
DDL_FILE = BE_ROOT / "clockyn_ddl_latest.sql"
MIGRATIONS_DIR = BE_ROOT / "migrations"
MAX_VARIANTS_APPROX = 6
MAX_TYPE_ATTEMPTS = 6
PREPARABLE = (A.SelectStmt, A.InsertStmt, A.UpdateStmt, A.DeleteStmt)
if hasattr(A, "MergeStmt"):
    PREPARABLE += (A.MergeStmt,)
TYPE_ROTATION = ["text", "integer", "date", "timestamptz", "boolean", "numeric", "uuid"]


# ------------------------------------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------------------------------------


def _literal_call_args(call: ast.Call):
    """(args, kwargs) when every argument of the call is a literal constant, else None."""
    args = []
    for a in call.args:
        if not isinstance(a, ast.Constant):
            return None
        args.append(a.value)
    kwargs = {}
    for k in call.keywords:
        if k.arg is None or not isinstance(k.value, ast.Constant):
            return None
        kwargs[k.arg] = k.value.value
    return args, kwargs


_BaseResolver = S._Resolver


class _FormatResolver(_BaseResolver):
    """The static checker's resolver, plus: `"...{x}...".format(x="LIT")` with literal arguments is formatted for real."""

    def resolve(self, node, scope, line, depth=0):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            lit = _literal_call_args(node)
            if lit is not None:
                recv = _BaseResolver.resolve(self, node.func.value, scope, line, depth + 1)
                if recv and all(all(isinstance(t, str) for t in v.tokens) for v in recv):
                    try:
                        return [S.Var(["".join(v.tokens).format(*lit[0], **lit[1])], v.line) for v in recv]
                    except (KeyError, IndexError, ValueError):
                        pass
        return _BaseResolver.resolve(self, node, scope, line, depth)


def _norm(text: str, n: int = 100000) -> str:
    return " ".join(text.split())[:n]


def to_pg(text: str):
    """psycopg2 -> PostgreSQL parameters. Every placeholder gets its OWN `$n` (psycopg2 sends independent literals even
    for a repeated `%(name)s`). `IN %s` gets parentheses, `INTERVAL %s` becomes a cast, `%%` becomes `%`.
    Returns (pg_text, number_of_parameters)."""
    text = re.sub(r"(?i)\bIN\s+(%(?:\(\w+\))?s)", r"IN (\1)", text)
    text = re.sub(r"(?i)\bINTERVAL\s+(%(?:\(\w+\))?s)", r"(\1)::interval", text)
    out, i, n, k = [], 0, len(text), 0
    while i < n:
        c = text[i]
        if c != "%":
            out.append(c)
            i += 1
        elif text.startswith("%%", i):
            out.append("%")
            i += 2
        elif text.startswith("%s", i):
            k += 1
            out.append(f"${k}")
            i += 2
        else:
            m = re.match(r"%\(\w+\)s", text[i:])
            if m:
                k += 1
                out.append(f"${k}")
                i += m.end()
            else:
                out.append("%")
                i += 1
    return "".join(out), k


@dataclass
class Item:
    file: str
    line: int
    origin: str                 # execute | constant | orphan
    approximate: bool
    variants: list              # pg texts; the item is OK when ANY variant prepares
    parts: list = field(default_factory=list)   # texts of single optional `+=` pieces, used only when no variant passes
    all_of: list = field(default_factory=list)  # fully hinted statement: EVERY text must prepare
    key: str = ""
    raw: str = ""


def _item_key(file: str, text: str) -> str:
    return f"{file}|{hashlib.sha1(_norm(text).encode('utf-8', 'replace')).hexdigest()[:10]}"


def _approx_variants(tokens, limit=MAX_VARIANTS_APPROX):
    """Renderings of a statement with placeholders; only those the PostgreSQL parser accepts, in the checker's order."""
    ctxs = S._contexts(tokens)
    n = len(ctxs)
    attempts = [[0] * n]
    for i in range(n):
        for j in range(1, len(ctxs[i][0])):
            c = [0] * n
            c[i] = j
            attempts.append(c)
    for j in range(1, max((len(c[0]) for c in ctxs), default=1)):
        attempts.append([min(j, len(c[0]) - 1) for c in ctxs])
    out, seen = [], set()
    for ch in attempts[: S.MAX_PARSE_ATTEMPTS]:
        raw, _ = S.render(tokens, ch)
        pg, _ = to_pg(raw)
        if pg in seen:
            continue
        seen.add(pg)
        try:
            pglast.parse_sql(pg)
        except pglast.parser.ParseError:
            continue
        out.append((pg, raw))
        if len(out) >= limit:
            break
    if not out:  # nothing parses: keep the default rendering so the syntax error is reported
        raw, _ = S.render(tokens, None)
        out.append((to_pg(raw)[0], raw))
    return out


def _scope_hints():
    """Real predicates produced by core_functions/scope.py (pure Python) for `scope_sql` style placeholders."""
    try:
        sys.path.insert(0, str(BE_ROOT))
        from core_functions import scope as sc
        out = []
        mgr = sc.make_scope("u1", 2, "o1", [1], ["d1"])
        for scope_obj in (mgr, sc.make_scope("u1", 3, "o1"), sc.make_scope("u1", 1, "o1")):
            for alias in ("u", None):
                out.append(sc.clause(scope_obj, alias)[0])
                out.append(sc.scope_predicate(scope_obj, alias, include_org=False)[0])
                out.append(sc.scope_predicate(scope_obj, alias, include_org=True)[0])
        return [x for x in dict.fromkeys(out) if x not in ("", "TRUE")]
    except Exception:  # pragma: no cover - the helper is optional
        return []


def _hinted_texts(tokens, rel, hints):
    """Texts of a statement whose every placeholder has a hint (scripts/sql_live_baseline.json `placeholder_hints`,
    key `<file>::<expr>` or `<expr>`); candidate i of every placeholder is used together in text i. None otherwise."""
    cands = []
    for t in tokens:
        if isinstance(t, str):
            continue
        c = hints.get(f"{rel}::{t.expr}") or hints.get(t.expr)
        if not c:
            return None
        cands.append(c)
    n = max((len(c) for c in cands), default=0)
    if n == 0:
        return None
    out = []
    for i in range(n):
        k = 0
        parts = []
        for t in tokens:
            if isinstance(t, str):
                parts.append(t)
            else:
                parts.append(cands[k][i % len(cands[k])])
                k += 1
        out.append("".join(parts))
    return out


_SQLISH = re.compile(
    r"^\s*(?:select\b.*\bfrom\b|insert\s+into\b|update\s+\S+\s+set\b|delete\s+from\b|with\s+\S+\s+as\s*\()", re.I | re.S)


def collect_items(paths=None):
    """(items, stats). Items are de-duplicated per file by statement text."""
    S._Resolver = _FormatResolver  # extract_statements instantiates the module-level class
    stats = {"extracted": 0, "unresolved_calls": []}
    hints = load_baseline().get("placeholder_hints", {})
    scope_alts = _scope_hints()
    items: dict[str, Item] = {}
    for f in S.collect_files(paths):
        rel = S._rel(f)
        source = f.read_text(encoding="utf-8-sig")
        stmts = S.extract_statements(source, rel)
        stats["extracted"] += len(stmts)
        covered = []
        for st in stmts:
            var = st.var
            all_of, approximate = [], st.approximate
            if st.approximate:
                hinted = _hinted_texts(var.tokens, rel, hints)       # every placeholder hinted: all texts must pass
                if hinted:
                    approximate = False
                    all_of = [to_pg(h)[0] for h in hinted]
                else:                                                 # scope predicates: any alias convention may pass
                    hinted = _hinted_texts(var.tokens, rel, {"scope_sql": scope_alts, "{scope_sql}": scope_alts})
                if hinted:
                    variants = [to_pg(h)[0] for h in hinted]
                    raw = hinted[0]
                    parts = []
                else:
                    vs = _approx_variants(var.tokens)
                    variants = [pg for pg, _ in vs]
                    raw = vs[0][1]
                    parts = []
            else:
                raw = S.render(var.tokens, None)[0]
                variants = [to_pg(raw)[0]]
                parts = []
                if var.optional:
                    for i in range(len(var.optional)):
                        piece = S.render(S._tokens_without(var, {i}), None)[0]
                        parts.append(to_pg(piece)[0])
            for pg in variants:
                covered.append(_norm(pg))
            covered.append(_norm(raw))
            key = _item_key(rel, variants[0])
            if (rel, key) not in items:
                items[(rel, key)] = Item(rel, st.line, "execute", approximate, variants, parts, all_of, key, _norm(raw, 300))
        # constants not passed to execute() directly (dict values, helper arguments, unresolved variables)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        inside_fstring = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in ast.walk(n)}
        cov_text = " || ".join(covered)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside_fstring:
                if not _SQLISH.match(n.value) or re.search(r"\{\w*\}", n.value) or _norm(n.value) in cov_text:
                    continue
                pg, _ = to_pg(n.value)
                key = _item_key(rel, pg)
                if (rel, key) not in items:
                    items[(rel, key)] = Item(rel, n.lineno, "orphan", False, [pg], [], [], key, _norm(n.value, 300))
    return list(items.values()), stats


# ------------------------------------------------------------------------------------------------
# Validation against PostgreSQL
# ------------------------------------------------------------------------------------------------


@dataclass
class Result:
    item: Item
    cls: str                      # OK | UNTYPED | REAL | APPROX | UTILITY
    error: str = ""
    code: str = ""
    snippet: str = ""
    types: dict = field(default_factory=dict)

    @property
    def severity(self) -> str:
        if self.cls != "REAL":
            return "info"
        return "warn" if (self.item.approximate or self.item.origin == "orphan") else "error"

    def to_dict(self):
        i = self.item
        return {"key": i.key, "file": i.file, "line": i.line, "class": self.cls, "severity": self.severity,
                "approximate": i.approximate, "origin": i.origin, "code": self.code, "error": self.error,
                "snippet": self.snippet or i.raw[:200], "declared_types": {str(k): v for k, v in self.types.items()}}


_OP_RE = re.compile(r"operator does not exist: (.+?) ([!-/:-@\[-`{-~]+) (.+?)\s*$")
_COL_RE = re.compile(r'is of type (.+?) but expression is of type (.+?)\s*$')


class Validator:
    """Runs PREPARE on a read-only session whose search_path is the scratch schema only."""

    def __init__(self, conn, schema: str):
        import pg_scratch
        assert pg_scratch._NAME_RE.match(schema)
        self.conn = conn
        self.schema = schema
        conn.rollback()
        conn.autocommit = True
        self.cur = conn.cursor()
        self.cur.execute(f'SET search_path TO "{schema}"')
        self.cur.execute("SET default_transaction_read_only = on")
        self.cur.execute("SET statement_timeout = '30s'")
        self.cur.execute("SET plan_cache_mode = force_generic_plan")
        self.cur.execute("SELECT current_schemas(false), current_setting('default_transaction_read_only')")
        schemas, ro = self.cur.fetchone()
        assert list(schemas) == [schema] and ro == "on", "validation session is not confined to the scratch schema"

    # -- single statement ------------------------------------------------------------------
    @staticmethod
    def _single_dml(pg_text: str):
        """('ok', text) when the text is exactly one preparable statement; ('utility', None); ('syntax', msg)."""
        try:
            tree = pglast.parse_sql(pg_text)
        except pglast.parser.ParseError as exc:
            return "syntax", str(exc)
        if len(tree) != 1:
            return "utility", None
        if not isinstance(tree[0].stmt, PREPARABLE):
            return "utility", None
        return "ok", pg_text

    def _prepare_once(self, text: str, nparams: int, types: dict):
        decl = ", ".join(types.get(i, "unknown") for i in range(1, nparams + 1))
        sent = f"PREPARE clk_s({decl}) AS {text}" if nparams else f"PREPARE clk_s AS {text}"
        import psycopg2
        phase = "prepare"
        try:
            self.cur.execute("BEGIN READ ONLY")
            self.cur.execute(sent)
            # planning pass: EXPLAIN without ANALYZE plans the statement (ON CONFLICT arbiter indexes, ...) and never
            # executes it; generic plan so NULL arguments are not constant-folded
            phase = "plan"
            args = ", ".join(["NULL"] * nparams)
            self.cur.execute(f"EXPLAIN (COSTS OFF) EXECUTE clk_s({args})" if nparams else "EXPLAIN (COSTS OFF) EXECUTE clk_s")
            self.cur.execute("DEALLOCATE clk_s; ROLLBACK")
            return None, sent
        except psycopg2.Error as exc:
            exc.clk_phase = phase
            try:
                self.cur.execute("ROLLBACK")
                self.cur.execute("DEALLOCATE ALL")
            except psycopg2.Error:  # pragma: no cover - connection problem
                pass
            return exc, sent

    @staticmethod
    def _nearest_param(sent: str, pos: int):
        best, dist = None, None
        for m in re.finditer(r"\$(\d+)", sent):
            if m.start() < sent.find(" AS ") + 4:  # skip the declaration list
                continue
            d = min(abs(m.start() - pos), abs(m.end() - pos))
            if dist is None or d < dist:
                best, dist = int(m.group(1)), d
        return best if dist is not None and dist <= 60 else None

    @staticmethod
    def _next_type(cur_type, preferred=None):
        if preferred and preferred not in ("unknown", cur_type):
            return preferred
        if cur_type in TYPE_ROTATION:
            return TYPE_ROTATION[(TYPE_ROTATION.index(cur_type) + 1) % len(TYPE_ROTATION)]
        return "text"

    def prepare_text(self, pg_text: str):
        """('OK'|'UNTYPED'|'UTILITY'|'REAL', error, code, snippet, declared types)."""
        pg_text = pg_text.replace("__SCHEMA__", f'"{self.schema}"')
        kind, payload = self._single_dml(pg_text)
        if kind == "syntax":
            return "REAL", payload.split(", at index")[0], "42601", "", {}
        if kind == "utility":
            return "UTILITY", "", "", "", {}
        nparams = max([int(x) for x in re.findall(r"\$(\d+)", pg_text)] or [0])
        types: dict = {}
        untyped = False
        err = None
        for _ in range(MAX_TYPE_ATTEMPTS + 1):
            exc, sent = self._prepare_once(pg_text, nparams, types)
            if exc is None:
                return ("UNTYPED" if untyped else "OK"), "", "", "", types
            err = exc
            msg = (exc.diag.message_primary or str(exc)).strip()
            code = exc.pgcode or ""
            if getattr(exc, "clk_phase", "") == "plan":     # planner error: nothing to retry
                return "REAL", "(plan) " + msg, code, "", types
            pos = int(exc.diag.statement_position or 0)
            m = re.search(r"parameter \$(\d+)", msg)
            if code in ("42P18", "42P08") and m:           # could not determine / inconsistent types for parameter
                untyped = True
                types[int(m.group(1))] = "text"
                continue
            p = self._nearest_param(sent, pos - 1) if pos else None
            if code in ("42883", "42804", "42725", "22P02", "42846") and p:
                cur_t = types.get(p, "unknown")
                pref = None
                mo = _OP_RE.search(msg)
                mc = _COL_RE.search(msg)
                if mo:
                    left, _op, right = mo.groups()
                    pref = left if right == cur_t else (right if left == cur_t else None)
                    if code == "42725" or cur_t == "unknown":
                        pref = pref or "text"
                elif mc:
                    pref = mc.group(1)
                elif "must be type boolean" in msg:
                    pref = "boolean"
                new_t = self._next_type(cur_t, pref)
                if new_t == cur_t:
                    break
                types[p] = new_t
                continue
            if code == "42803" and nparams > 1:
                # psycopg2 sends literals, so `GROUP BY CASE WHEN a = %s ...` equals the select-list expression; a
                # server-side prepared statement sees $1 != $4. Re-check with every parameter replaced by NULL.
                lit = re.sub(r"\$\d+", "NULL", pg_text)
                exc2, _sent2 = self._prepare_once(lit, 0, {})
                if exc2 is None:
                    return "OK", "", "", "", {}
            break
        exc = err
        msg = (exc.diag.message_primary or str(exc)).strip()
        pos = int(exc.diag.statement_position or 0)
        snippet = ""
        if pos:
            body = sent[sent.find(" AS ") + 4:]
            off = pos - 1 - (sent.find(" AS ") + 4)
            snippet = _norm(body[max(0, off - 50): off + 70], 140)
        return "REAL", msg, exc.pgcode or "", snippet, types

    # -- item -----------------------------------------------------------------------------
    def check(self, item: Item) -> Result:
        if item.all_of:
            res = None
            for pg in item.all_of:
                cls, msg, code, snip, types = self.prepare_text(pg)
                if cls == "REAL":
                    return Result(item, "REAL", msg, code, snip, types)
                res = res or (cls, types)
            return Result(item, res[0] if res else "OK", types=res[1] if res else {})
        first = None
        for pg in item.variants:
            cls, msg, code, snip, types = self.prepare_text(pg)
            if cls in ("OK", "UNTYPED", "UTILITY"):
                return Result(item, cls, types=types)
            if first is None:
                first = (msg, code, snip, types)
        if item.parts:  # the combination of optional pieces may be contradictory: every single piece must still be valid
            bad = None
            for pg in item.parts:
                cls, msg, code, snip, types = self.prepare_text(pg)
                if cls == "REAL" and bad is None:
                    bad = (msg, code, snip, types)
            if bad is None:
                return Result(item, "OK")
            first = bad
        msg, code, snip, types = first
        if item.approximate:
            return Result(item, "APPROX", msg, code, snip, types)
        return Result(item, "REAL", msg, code, snip, types)


# ------------------------------------------------------------------------------------------------
# Migrations (DDL applied inside a rolled-back transaction in the scratch schema)
# ------------------------------------------------------------------------------------------------

_FORBIDDEN_STATEMENTS = (
    r"(?i)\b(?:create\s+schema|drop\s+schema|create\s+database|drop\s+database|alter\s+database|alter\s+system|"
    r"create\s+role|drop\s+role|alter\s+role|create\s+extension|drop\s+extension|set\s+role|set\s+session|"
    r"copy\s+\S+.*\bprogram\b|clockyn_dev\b|public\.")


def _forbidden_migration_pattern(baseline_schema: str):
    """Statement guard plus 'a schema of the real databases is named' (the baseline qualifier was already rewritten to the
    scratch schema, so any remaining name of that family is foreign)."""
    family = baseline_schema.split("_")[0]
    return re.compile(_FORBIDDEN_STATEMENTS + rf"|{family}_(?:dev|qa|backup|prod)\b)")


def apply_migrations(conn, schema: str):
    """[(file, ok, error)] applying each migration in its own transaction that is always rolled back."""
    import psycopg2
    import pg_scratch
    assert pg_scratch._NAME_RE.match(schema)
    guard = _forbidden_migration_pattern(pg_scratch.BASELINE_SCHEMA)
    results = []
    conn.rollback()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(f'SET search_path TO "{schema}"')
    conn.commit()
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = pg_scratch.rewrite_ddl(f.read_text(encoding="utf-8-sig"), schema)
        bad = guard.search(text)
        if bad:
            results.append((f.name, False, f"refused by the safety guard: {bad.group(0)!r}"))
            continue
        try:
            cur.execute(text)
            results.append((f.name, True, ""))
        except psycopg2.Error as exc:
            results.append((f.name, False, (exc.diag.message_primary or str(exc)).strip()))
        finally:
            conn.rollback()
    return results


# ------------------------------------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------------------------------------


def load_baseline(path=BASELINE_FILE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"untyped": [], "accepted_errors": []}


def run_live_check(paths=None, with_migrations=True):
    """Creates the scratch schema, validates, drops the scratch schema (always). Returns a report dict."""
    import pg_scratch
    items, stats = collect_items(paths)
    conn = pg_scratch.connect()
    schema = None
    vconn = None
    try:
        schema = pg_scratch.create_scratch_schema(conn)
        cur = conn.cursor()
        cur.execute(f'SET search_path TO "{schema}"')
        ddl = pg_scratch.rewrite_ddl(DDL_FILE.read_text(encoding="utf-8-sig"), schema)
        cur.execute(ddl)
        cur.execute("CREATE TABLE schema_migrations (version text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]
        vconn = pg_scratch.connect()
        validator = Validator(vconn, schema)
        results = [validator.check(it) for it in items]
        vconn.close()
        vconn = None
        migrations = apply_migrations(conn, schema) if with_migrations else []
    finally:
        for c in (vconn,):
            try:
                if c is not None:
                    c.close()
            except Exception:  # pragma: no cover
                pass
        try:
            conn.rollback()
        except Exception:  # pragma: no cover
            pass
        try:
            if schema:
                pg_scratch.drop_scratch_schema(conn, schema)
        finally:
            conn.close()
    return build_report(items, stats, results, migrations, server_version)


def build_report(items, stats, results, migrations, server_version=""):
    base = load_baseline()
    accepted = {a["key"] for a in base.get("accepted_errors", [])}
    counts = {}
    for r in results:
        counts[r.cls] = counts.get(r.cls, 0) + 1
    real = [r for r in results if r.cls == "REAL" and r.severity == "error" and r.item.key not in accepted]
    warn = [r for r in results if r.cls == "REAL" and r.severity == "warn" and r.item.key not in accepted]
    approx = [r for r in results if r.cls == "APPROX"]
    untyped_new = [r for r in results if r.cls == "UNTYPED" and r.item.key not in set(base.get("untyped", []))]
    mig_bad = [m for m in migrations if not m[1]]
    return {
        "server_version": server_version,
        "extracted_calls": stats["extracted"],
        "statements_checked": len(items),
        "approximate": sum(1 for i in items if i.approximate),
        "orphan_constants": sum(1 for i in items if i.origin == "orphan"),
        "counts": counts,
        "real_errors": [r.to_dict() for r in real],
        "warnings": [r.to_dict() for r in warn],
        "approx_failures": [r.to_dict() for r in approx],
        "untyped_not_in_baseline": [r.to_dict() for r in untyped_new],
        "untyped_keys": sorted(r.item.key for r in results if r.cls == "UNTYPED"),
        "migrations": [{"file": f, "ok": ok, "error": e} for f, ok, e in migrations],
        "migration_failures": len(mig_bad),
        "_results": results,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate every backend SQL statement with PostgreSQL (PREPARE only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--paths", nargs="*", default=None)
    ap.add_argument("--no-migrations", action="store_true")
    ap.add_argument("--all", action="store_true", help="also list warnings (approximate/orphan) and UNTYPED statements")
    ap.add_argument("--write-baseline", action="store_true", help="store the current UNTYPED keys in the baseline file")
    args = ap.parse_args(argv)
    try:
        rep = run_live_check(args.paths, not args.no_migrations)
    except BaseException as exc:  # pytest.skip(...) from pg_scratch.settings() when no DB is configured
        if type(exc).__name__ in ("Skipped", "OutcomeException"):
            print("no database configured (TEST_PG_* or DB_* in .env): nothing checked")
            return 2
        raise
    results = rep.pop("_results")
    if args.write_baseline:
        base = load_baseline()
        base["untyped"] = rep["untyped_keys"]
        base.setdefault("accepted_errors", [])
        BASELINE_FILE.write_text(json.dumps(base, indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {len(base['untyped'])} untyped keys")
    failed = bool(rep["real_errors"]) or rep["migration_failures"] > 0
    if args.json:
        if args.all:
            rep["all_results"] = [r.to_dict() for r in results]
        print(json.dumps(rep, indent=1))
        return 1 if failed else 0
    print(f"PostgreSQL {rep['server_version']}: {rep['statements_checked']} distinct statements "
          f"({rep['extracted_calls']} execute() call variants, {rep['approximate']} approximate, "
          f"{rep['orphan_constants']} constants outside execute())")
    print("classes: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["counts"].items())))
    print(f"migrations: {sum(m['ok'] for m in rep['migrations'])}/{len(rep['migrations'])} applied (rolled back)")
    for m in rep["migrations"]:
        if not m["ok"]:
            print(f"  MIGRATION FAIL {m['file']}: {m['error']}")
    for r in rep["real_errors"]:
        print(f"error | {r['file']}:{r['line']} | {r['code']} {r['error']} | {r['snippet']}")
    if args.all:
        for r in rep["warnings"]:
            print(f"warn  | {r['file']}:{r['line']} | {r['code']} {r['error']} | {r['snippet']}")
        for r in rep["approx_failures"]:
            print(f"approx | {r['file']}:{r['line']} | {r['code']} {r['error']} | {r['snippet'][:120]}")
        for r in rep["untyped_not_in_baseline"]:
            print(f"untyped (not in baseline) | {r['file']}:{r['line']} | {r['snippet'][:100]}")
    print(f"real errors: {len(rep['real_errors'])}; warnings (approximate/orphan): {len(rep['warnings'])}; "
          f"untyped outside baseline: {len(rep['untyped_not_in_baseline'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
