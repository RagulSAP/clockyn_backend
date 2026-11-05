#!/usr/bin/env python3
"""Static SQL checker: validates the SQL embedded in the backend Python code without a database.

What it does
  1. Walks the Python sources with `ast` and collects every SQL string passed to
     `.execute(` / `.executemany(` (string constants, implicit concatenation, triple quoted strings,
     f-strings, `"...".format(...)`, `a + b`, and simple variables assigned earlier in the same
     function, including `query += "..."` accumulation).
  2. Converts psycopg2 placeholders (`%s`, `%(name)s`, `%%`) to PostgreSQL `$N` parameters, counts them
     and compares the count with a literal params tuple/list/dict (PARAM_COUNT_MISMATCH).
  3. Parses every statement with the real PostgreSQL parser (pglast): SYNTAX_ERROR.
  4. Resolves aliases against the schema in punchapp_ddl_latest.sql and flags UNKNOWN_TABLE,
     UNKNOWN_COLUMN and UNKNOWN_ALIAS for qualified/unqualified column references and for
     INSERT / UPDATE target columns.

Statements with f-string `{...}` parts that could not be resolved are "approximate": each unresolved
part is replaced with a harmless identifier / predicate (several candidates are tried until the
statement parses), and only the lower-risk checks run on them (qualified columns of known tables).

Usage (from the backend root):
  python scripts/sql_static_check.py [--json] [--paths P ...] [--baseline FILE]
                                     [--write-baseline FILE] [--ddl FILE] [--all]
Exit code 1 when there are `error` findings that are not in the baseline.
Severities: `error` (syntax, param mismatch, unknown column of a known table) and `warn`.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

BE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DDL = BE_ROOT / "punchapp_ddl_latest.sql"
DEFAULT_BASELINE = BE_ROOT / "scripts" / "sql_static_baseline.json"
SCAN_GLOBS = [
    "core_functions/*.py", "router/*.py", "services/*.py", "utils/*.py", "ml/*.py",
    "migrate.py", "app.py", "socket_manager.py",
]
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

PH = "x_placeholder"
MAX_VARIANTS = 8
MAX_PARSE_ATTEMPTS = 150

# ------------------------------------------------------------------------------------------------
# Findings
# ------------------------------------------------------------------------------------------------


@dataclass
class Finding:
    file: str
    line: int
    code: str
    severity: str
    message: str
    sql: str = ""
    approximate: bool = False
    key: str = ""

    def to_dict(self):
        return {
            "key": self.key, "file": self.file, "line": self.line, "code": self.code,
            "severity": self.severity, "message": self.message, "sql": self.sql,
            "approximate": self.approximate,
        }


def _norm(s: str, n: int = 400) -> str:
    return " ".join(s.split())[:n]


def finding_key(f: Finding) -> str:
    h = hashlib.sha1((f.message + "|" + _norm(f.sql)).encode("utf-8", "replace")).hexdigest()[:10]
    return f"{f.file}|{f.code}|{h}"


# ------------------------------------------------------------------------------------------------
# Part 1: extraction of SQL from Python source
# ------------------------------------------------------------------------------------------------


@dataclass
class Placeholder:
    expr: str
    simple: bool = True  # Name / Attribute (vs. call, join, subscript ...)


@dataclass
class Var:
    """One resolved SQL text: tokens are str or Placeholder."""
    tokens: list
    line: int
    optional: list = field(default_factory=list)  # token lists of `+=` pieces (already inside tokens)


@dataclass
class ParamsInfo:
    kind: str  # 'none' | 'seq' | 'dict' | 'unknown'
    count: int = 0
    names: tuple = ()


@dataclass
class SqlStatement:
    file: str
    line: int
    call_line: int
    var: Var
    params: ParamsInfo
    func: str = "execute"

    @property
    def tokens(self):
        return self.var.tokens

    @property
    def approximate(self) -> bool:
        return any(isinstance(t, Placeholder) for t in self.var.tokens)

    @property
    def sql(self) -> str:
        """Default rendering (first substitution candidate for every placeholder), psycopg2 style."""
        return render(self.var.tokens, None)[0]


@dataclass
class _Assign:
    lineno: int
    kind: str  # '=' or '+='
    value: ast.AST
    path: tuple  # ((compound_stmt_id, arm), ...) from the scope body down to the statement


class _Scope:
    def __init__(self, parent):
        self.parent = parent
        self.assigns: dict[str, list[_Assign]] = {}
        self.call_paths: dict[int, tuple] = {}
        self.term_arms: set = set()  # (compound id, arm) whose body ends in return/raise/continue/break

    def add(self, name, a: _Assign):
        self.assigns.setdefault(name, []).append(a)


def _src(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return "?"


def _collect(stmts, sc: _Scope, path: tuple, scopes: dict):
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _build(st, sc, scopes)
            continue
        if isinstance(st, ast.ClassDef):
            for sub in st.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _build(sub, sc, scopes)
            continue
        compound = False
        arms = []
        for fname in ("body", "orelse", "finalbody"):
            sub = getattr(st, fname, None)
            if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                compound = True
                arms.append((fname, sub))
        for i, h in enumerate(getattr(st, "handlers", []) or []):
            compound = True
            arms.append((f"handler{i}", h.body))
        for i, c in enumerate(getattr(st, "cases", []) or []):
            compound = True
            arms.append((f"case{i}", c.body))
        if compound:
            plain_with = isinstance(st, (ast.With, ast.AsyncWith))
            for arm, body in arms:
                if isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                    sc.term_arms.add((id(st), arm))
                _collect(body, sc, path if plain_with else path + ((id(st), arm),), scopes)
            header = []
            for fname in ("test", "iter", "items", "subject"):
                v = getattr(st, fname, None)
                header += v if isinstance(v, list) else ([v] if v is not None else [])
            for h in header:
                for n in ast.walk(h):
                    if isinstance(n, ast.Call):
                        sc.call_paths[id(n)] = path
            continue
        if isinstance(st, ast.Assign):
            for tg in st.targets:
                if isinstance(tg, ast.Name):
                    sc.add(tg.id, _Assign(st.lineno, "=", st.value, path))
        elif isinstance(st, ast.AnnAssign) and st.value is not None and isinstance(st.target, ast.Name):
            sc.add(st.target.id, _Assign(st.lineno, "=", st.value, path))
        elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and isinstance(st.op, ast.Add):
            sc.add(st.target.id, _Assign(st.lineno, "+=", st.value, path))
        for n in ast.walk(st):
            if isinstance(n, ast.Call):
                sc.call_paths[id(n)] = path


def _compatible(assign_path: tuple, use_path: tuple, term_arms: set = frozenset()) -> bool:
    """False when the assignment sits in a different arm of an if/try/match that also holds the use,
    or in an arm that always returns/raises while the use is outside that arm."""
    arms = dict(use_path)
    for nid, arm in assign_path:
        if nid in arms and arms[nid] != arm:
            return False
        if (nid, arm) in term_arms and arms.get(nid) != arm:
            return False
    return True


def _dominates(assign_path: tuple, use_path: tuple) -> bool:
    return use_path[: len(assign_path)] == assign_path


def _build(node, parent, scopes):
    sc = _Scope(parent)
    scopes[id(node)] = sc
    _collect(node.body, sc, (), scopes)
    return sc


def _parse_format_fields(text: str):
    """Split a str.format template into str / Placeholder tokens; None when it is not parseable."""
    out = []
    try:
        for lit, field_name, _spec, _conv in string.Formatter().parse(text):
            if lit:
                out.append(lit)
            if field_name is not None:
                out.append(Placeholder("{" + field_name + "}", True))
    except ValueError:
        return None
    return out


class _Resolver:
    def __init__(self):
        self.depth_limit = 6
        self.path: tuple = ()  # arm path of the use site being resolved

    # -- helpers
    @staticmethod
    def _cross(a, b):
        out = []
        for x in a:
            for y in b:
                out.append(Var(x.tokens + y.tokens, x.line))
                if len(out) >= MAX_VARIANTS:
                    return out
        return out

    def _lookup(self, name, scope):
        sc = scope
        while sc is not None:
            if name in sc.assigns:
                return sc.assigns[name], sc
            sc = sc.parent
        return None, None

    def resolve_name(self, name, scope, line, depth):
        entries, owner = self._lookup(name, scope)
        if not entries:
            return None
        before = [e for e in entries if e.lineno < line] if owner is scope else list(entries)
        use_path = self.path if owner is scope else ()
        before = [e for e in before if _compatible(e.path, use_path, owner.term_arms)]
        bases = [e for e in before if e.kind == "="]
        if not bases:
            return None
        dom = None
        for e in bases:
            if _dominates(e.path, use_path):
                dom = e
        if dom is not None:
            bases = [e for e in bases if e.lineno >= dom.lineno]
        bases = bases[-4:]
        results = []
        saved = self.path
        for b in bases:
            self.path = b.path
            base_res = self.resolve(b.value, owner, b.lineno, depth + 1)
            if not base_res:
                continue
            augs = [e for e in before if e.kind == "+=" and e.lineno > b.lineno]
            opt_tokens = []
            for a in augs:
                self.path = a.path
                ar = self.resolve(a.value, owner, a.lineno, depth + 1)
                opt_tokens.append(ar[0].tokens if ar else [Placeholder(_src(a.value), False)])
            for bv in base_res:
                toks = list(bv.tokens)
                for ot in opt_tokens:
                    toks += ot
                results.append(Var(toks, bv.line, list(opt_tokens)))
        self.path = saved
        return results or None

    # -- main
    def resolve(self, node, scope, line, depth=0):
        if depth > self.depth_limit:
            return None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return [Var([node.value], node.lineno)]
            return None
        if isinstance(node, ast.JoinedStr):
            acc = [Var([], node.lineno)]
            for v in node.values:
                if isinstance(v, ast.Constant):
                    piece = [Var([str(v.value)], node.lineno)]
                elif isinstance(v, ast.FormattedValue):
                    piece = None
                    if isinstance(v.value, ast.Name) and v.format_spec is None and v.conversion == -1:
                        piece = self.resolve_name(v.value.id, scope, v.lineno, depth + 1)
                        if piece:
                            piece = [Var(list(p.tokens), p.line) for p in piece]
                    if not piece:
                        simple = isinstance(v.value, (ast.Name, ast.Attribute))
                        piece = [Var([Placeholder("{" + _src(v.value) + "}", simple)], node.lineno)]
                else:
                    piece = [Var([Placeholder(_src(v), False)], node.lineno)]
                acc = self._cross(acc, piece)
            return acc
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.resolve(node.left, scope, line, depth + 1)
            right = self.resolve(node.right, scope, line, depth + 1)
            if left is None and right is None:
                return None
            if left is None:
                left = [Var([Placeholder(_src(node.left), False)], node.lineno)]
            if right is None:
                right = [Var([Placeholder(_src(node.right), False)], node.lineno)]
            return self._cross(left, right)
        if isinstance(node, ast.IfExp):
            a = self.resolve(node.body, scope, line, depth + 1)
            b = self.resolve(node.orelse, scope, line, depth + 1)
            if a is None and b is None:
                return None
            a = a or [Var([Placeholder(_src(node.body), False)], node.lineno)]
            b = b or [Var([Placeholder(_src(node.orelse), False)], node.lineno)]
            return (a + b)[:MAX_VARIANTS]
        if isinstance(node, ast.Name):
            return self.resolve_name(node.id, scope, line, depth + 1)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("strip", "lstrip", "rstrip"):
                return self.resolve(f.value, scope, line, depth + 1)
            if (isinstance(f, ast.Attribute) and f.attr == "dedent") or (isinstance(f, ast.Name) and f.id == "dedent"):
                if node.args:
                    return self.resolve(node.args[0], scope, line, depth + 1)
                return None
            if isinstance(f, ast.Attribute) and f.attr == "format":
                recv = self.resolve(f.value, scope, line, depth + 1)
                if not recv:
                    return None
                out = []
                for v in recv:
                    toks = []
                    for t in v.tokens:
                        if isinstance(t, str):
                            sub = _parse_format_fields(t)
                            if sub is None:
                                return None
                            toks += sub
                        else:
                            toks.append(t)
                    out.append(Var(toks, v.line))
                return out
            return None
        return None


def _params_info(call: ast.Call, sql_index: int, is_many: bool) -> ParamsInfo:
    kws = {k.arg: k.value for k in call.keywords if k.arg}
    if len(call.args) > sql_index + 1:
        p = call.args[sql_index + 1]
    elif "vars" in kws:
        p = kws["vars"]
    elif "argslist" in kws:
        p = kws["argslist"]
    elif len(call.args) == sql_index + 1 and not call.keywords:
        return ParamsInfo("none")
    else:
        return ParamsInfo("unknown")
    if is_many:
        if isinstance(p, (ast.List, ast.Tuple)) and p.elts and all(isinstance(e, (ast.Tuple, ast.List)) for e in p.elts):
            lens = {len(e.elts) for e in p.elts}
            if len(lens) == 1 and not any(isinstance(x, ast.Starred) for e in p.elts for x in e.elts):
                return ParamsInfo("seq", lens.pop())
        return ParamsInfo("unknown")
    if isinstance(p, (ast.Tuple, ast.List)):
        if any(isinstance(x, ast.Starred) for x in p.elts):
            return ParamsInfo("unknown")
        return ParamsInfo("seq", len(p.elts))
    if isinstance(p, ast.Dict):
        if all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in p.keys if k is not None) and None not in p.keys:
            return ParamsInfo("dict", len(p.keys), tuple(k.value for k in p.keys))
        return ParamsInfo("unknown")
    if isinstance(p, ast.Constant) and p.value is None:
        return ParamsInfo("none")
    return ParamsInfo("unknown")


_SQL_START = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*(select|insert|update|delete|with|create|alter|drop|set|truncate|begin|commit|"
    r"rollback|lock|values|explain|comment|grant|revoke|analyze|vacuum|reindex|savepoint|release|notify|"
    r"listen|call|copy|prepare|declare|show|reset)\b", re.I)


def extract_statements(source: str, filename: str = "<src>") -> list[SqlStatement]:
    """All SQL statements passed to execute()/executemany() in `source` (psycopg2 placeholders kept)."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    scopes: dict = {}
    module_scope = _build(tree, None, scopes)
    resolver = _Resolver()
    out: list[SqlStatement] = []
    stack = [module_scope]

    class V(ast.NodeVisitor):
        def _func(self, node):
            stack.append(scopes.get(id(node), stack[-1]))
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = _func
        visit_AsyncFunctionDef = _func

        def visit_Call(self, node):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            idx = None
            if name in ("execute", "executemany") and isinstance(f, ast.Attribute):
                idx = 0
            elif name in ("execute_values", "execute_batch"):
                idx = 1
            if idx is not None:
                arg = node.args[idx] if len(node.args) > idx else None
                if arg is None:
                    for k in node.keywords:
                        if k.arg in ("query", "sql"):
                            arg = k.value
                if arg is not None:
                    resolver.path = stack[-1].call_paths.get(id(node), ())
                    variants = resolver.resolve(arg, stack[-1], node.lineno)
                    if variants:
                        pinfo = _params_info(node, idx, name == "executemany")
                        seen = set()
                        for v in variants:
                            first = v.tokens[0] if v.tokens else ""
                            if not isinstance(first, str) or not _SQL_START.match(first):
                                continue
                            sig = tuple(t if isinstance(t, str) else "\0" + t.expr for t in v.tokens)
                            if sig in seen:
                                continue
                            seen.add(sig)
                            out.append(SqlStatement(filename, v.line, node.lineno, v, pinfo, name))
            self.generic_visit(node)

    V().visit(tree)
    return out


# ------------------------------------------------------------------------------------------------
# Part 2: placeholder substitution and psycopg2 -> $N conversion
# ------------------------------------------------------------------------------------------------

_PCT = r"%(?:\(\w+\))?s"
_KW_IDENT_BEFORE = {"FROM", "JOIN", "INTO", "UPDATE", "TABLE", "AS"}
_KW_PRED_BEFORE = {"WHERE", "AND", "OR", "ON", "HAVING", "NOT", "WHEN", "THEN", "ELSE"}
_OPERATOR_START = re.compile(r"^\s*(?:=|<|>|!|\.|::|\|\||\+|\*|/|(?:in|like|ilike|is|between|not)\b)", re.I)
_SUFFIX_CANDS = ["", " AND 1 = 1 ", " ORDER BY x_placeholder ", " LIMIT 1 ", " x_placeholder ",
                 ", x_placeholder ", " JOIN x_placeholder ON 1 = 1 ", " x_placeholder = 1 "]


def _last_word(text: str) -> str:
    m = re.search(r"([A-Za-z_]+)\s*$", text)
    return m.group(1).upper() if m else ""


def _context_candidates(prev: str, nxt: str):
    """(candidate replacement texts, params_reliable) for a placeholder given its surroundings."""
    if prev.count("'") % 2 == 1:
        return [PH], True
    if _OPERATOR_START.match(nxt):
        return [PH], True
    word = _last_word(prev)
    stripped = prev.rstrip()
    if word in _KW_IDENT_BEFORE:
        return [PH], True
    if word in _KW_PRED_BEFORE:
        return ["1 = 1", PH], False
    if word == "SET":
        return [PH + " = 1", PH], False
    if word in ("LIMIT", "OFFSET"):
        return ["1", PH], True
    if word == "BY":
        return [PH, "1 = 1", ""], True
    if stripped.endswith("(") or stripped.endswith(",") or word in ("SELECT", "DISTINCT", "RETURNING"):
        return [PH, PH + " = 1", "1 = 1", ""], False
    if not stripped:
        return [PH], False
    return list(_SUFFIX_CANDS), False


def _contexts(tokens):
    """Per placeholder: (candidates, reliable). Uses the default (first) candidate for earlier ones."""
    ctxs = []
    text = ""
    for i, t in enumerate(tokens):
        if isinstance(t, str):
            text += t
        else:
            nxt = tokens[i + 1] if i + 1 < len(tokens) and isinstance(tokens[i + 1], str) else ""
            cands, rel = _context_candidates(text, nxt)
            if not (t.simple or rel):
                rel = False
            ctxs.append((cands, rel and t.simple))
            text += cands[0]
    return ctxs


def render(tokens, choices):
    """Render tokens to SQL text. choices: per-placeholder candidate index (None = all first).
    Returns (text, params_reliable) where params_reliable is False if a placeholder may hide `%s`."""
    ctxs = _contexts(tokens)
    out = []
    k = 0
    reliable = True
    for t in tokens:
        if isinstance(t, str):
            out.append(t)
        else:
            cands, rel = ctxs[k]
            idx = 0 if choices is None else choices[k]
            idx = min(idx, len(cands) - 1)
            out.append(cands[idx])
            if not rel:
                reliable = False
            k += 1
    return "".join(out), reliable


def convert_params(text: str):
    """psycopg2 -> PostgreSQL. Returns (pg_text, n_positional, named_names, bad_percent_positions).
    `%%` becomes `%`; `%s` and `%(name)s` become `$N` (a name reuses its number); `IN %s` gets
    parentheses (psycopg2 expands a tuple there), `VALUES %s` too, `INTERVAL %s` becomes a cast."""
    text = re.sub(r"(?i)\bIN\s+(" + _PCT + ")", r"IN (\1)", text)
    text = re.sub(r"(?i)\bVALUES\s+(" + _PCT + ")", r"VALUES (\1)", text)
    text = re.sub(r"(?i)\bINTERVAL\s+(" + _PCT + ")", r"(\1)::interval", text)
    out = []
    i, n = 0, len(text)
    pos = 0
    names: dict[str, int] = {}
    bad = []
    nxt = 1
    while i < n:
        c = text[i]
        if c != "%":
            out.append(c)
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "%":
            out.append("%")
            i += 2
        elif i + 1 < n and text[i + 1] == "s":
            out.append(f"${nxt}")
            nxt += 1
            pos += 1
            i += 2
        else:
            m = re.match(r"%\((\w+)\)s", text[i:])
            if m:
                if m.group(1) not in names:
                    names[m.group(1)] = nxt
                    nxt += 1
                out.append(f"${names[m.group(1)]}")
                i += m.end()
            else:
                bad.append(i)
                out.append("%")
                i += 1
    return "".join(out), pos, tuple(names), bad


# ------------------------------------------------------------------------------------------------
# Part 3: schema from the DDL and column analysis
# ------------------------------------------------------------------------------------------------


@dataclass
class Entry:
    cols: set
    open: bool = False  # unknown / partially unknown column set: never flag a missing column
    table: str | None = None  # real table name (for messages)


def load_schema(ddl_sql: str) -> dict:
    """{table: set(columns)} from CREATE TABLE / ALTER TABLE ADD|DROP COLUMN (schema qualifier stripped).
    Views and CREATE TABLE AS are registered as open (columns unknown): value None."""
    tables: dict = {}
    for rs in pglast.parse_sql(ddl_sql):
        s = rs.stmt
        if isinstance(s, A.CreateStmt):
            cols = {el.colname for el in (s.tableElts or ()) if isinstance(el, A.ColumnDef)}
            tables[s.relation.relname] = cols
        elif isinstance(s, A.AlterTableStmt) and s.relation is not None:
            t = tables.get(s.relation.relname)
            if t is None:
                continue
            for cmd in s.cmds or ():
                if cmd.subtype == E.AlterTableType.AT_AddColumn and isinstance(cmd.def_, A.ColumnDef):
                    t.add(cmd.def_.colname)
                elif cmd.subtype == E.AlterTableType.AT_DropColumn:
                    t.discard(cmd.name)
        elif isinstance(s, A.RenameStmt) and s.renameType == E.ObjectType.OBJECT_COLUMN and s.relation is not None:
            t = tables.get(s.relation.relname)
            if t is not None:
                t.discard(s.subname)
                t.add(s.newname)
        elif isinstance(s, (A.ViewStmt,)):
            tables[s.view.relname] = None
        elif isinstance(s, A.CreateTableAsStmt) and s.into is not None:
            tables[s.into.rel.relname] = None
    return tables


def _iter_nodes(v):
    if isinstance(v, A.Node):
        yield v
    elif isinstance(v, (tuple, list)):
        for x in v:
            yield from _iter_nodes(x)


def _children(node):
    for name in node.__slots__:
        v = getattr(node, name, None)
        if v is not None and not isinstance(v, (str, int, bool, float)):
            yield from _iter_nodes(v)


_SYSCOLS = {"ctid", "tableoid", "xmin", "xmax", "cmin", "cmax"}
_OK_SCHEMAS = (None, "punchapp_prod")


class _Scope2:
    def __init__(self, parent, ctes):
        self.parent = parent
        self.ctes = ctes
        self.entries: dict[str, Entry] = {}
        self.quals: list = []

    def chain(self):
        s = self
        while s is not None:
            yield s
            s = s.parent


class Analyzer:
    def __init__(self, schema: dict, approximate: bool, seen_tables: set, report):
        self.schema = schema
        self.approx = approximate
        self.seen_tables = seen_tables
        self.report = report  # report(code, severity, message)

    # ---- entry points
    def analyze(self, node):
        if isinstance(node, A.SelectStmt):
            self.analyze_select(node, None, {})
        elif isinstance(node, (A.InsertStmt, A.UpdateStmt, A.DeleteStmt)):
            self.analyze_dml(node, None, {})
        elif isinstance(node, A.ExplainStmt) and node.query is not None:
            self.analyze(node.query)
        elif isinstance(node, A.CreateTableAsStmt) and node.query is not None:
            self.analyze(node.query)

    def analyze_query(self, q, parent, ctes) -> Entry:
        if isinstance(q, A.SelectStmt):
            return self.analyze_select(q, parent, ctes)
        if isinstance(q, (A.InsertStmt, A.UpdateStmt, A.DeleteStmt)):
            self.analyze_dml(q, parent, ctes)
        return Entry(set(), True)

    # ---- tables
    def table_entry(self, rv: A.RangeVar, scope_ctes) -> Entry:
        name = rv.relname
        if rv.schemaname is None and name in scope_ctes:
            return scope_ctes[name]
        if name.startswith(PH) or name.startswith("pg_"):
            return Entry(set(), True)  # placeholder or system catalog
        if rv.schemaname in _OK_SCHEMAS:
            if name in self.schema:
                cols = self.schema[name]
                if cols is None:
                    return Entry(set(), True, name)
                return Entry(set(cols), False, name)
            if name not in self.seen_tables:
                self.seen_tables.add(name)
                self.report("UNKNOWN_TABLE", "warn", f"table '{name}' is not in the DDL")
        return Entry(set(), True, name)

    @staticmethod
    def _alias_cols(entry: Entry, alias) -> Entry:
        if alias is not None and alias.colnames:
            names = {c.sval for c in alias.colnames}
            return Entry(names, True, entry.table)
        return entry

    def add_from(self, item, scope: _Scope2):
        if isinstance(item, A.RangeVar):
            ent = self.table_entry(item, scope.ctes)
            alias = item.alias
            ent = self._alias_cols(ent, alias)
            scope.entries[alias.aliasname if alias else item.relname] = ent
        elif isinstance(item, A.RangeSubselect):
            parent = scope if item.lateral else scope.parent
            ent = self.analyze_query(item.subquery, parent, scope.ctes)
            ent = self._alias_cols(ent, item.alias)
            if item.alias is not None:
                scope.entries[item.alias.aliasname] = ent
        elif isinstance(item, A.RangeFunction):
            alias = item.alias
            for fn in _iter_nodes(item.functions):
                self.walk(fn, scope, set())
            if alias is not None:
                scope.entries[alias.aliasname] = Entry(
                    {c.sval for c in (alias.colnames or ())}, True)
        elif isinstance(item, A.JoinExpr):
            self.add_from(item.larg, scope)
            self.add_from(item.rarg, scope)
            if item.quals is not None:
                scope.quals.append(item.quals)
            if item.alias is not None:
                scope.entries[item.alias.aliasname] = Entry(set(), True)
        elif isinstance(item, A.Node):
            alias = getattr(item, "alias", None)
            if alias is not None:
                scope.entries[alias.aliasname] = Entry(set(), True)

    # ---- select
    def analyze_select(self, sel: A.SelectStmt, parent, ctes) -> Entry:
        ctes = dict(ctes)
        if sel.withClause is not None:
            for cte in sel.withClause.ctes:
                if sel.withClause.recursive:
                    ctes[cte.ctename] = Entry(set(), True)
                ent = self.analyze_query(cte.ctequery, parent, ctes)
                if cte.aliascolnames:
                    ent = Entry({c.sval for c in cte.aliascolnames}, True)
                ctes[cte.ctename] = ent
        if sel.op != E.SetOperation.SETOP_NONE:
            left = self.analyze_select(sel.larg, parent, ctes)
            self.analyze_select(sel.rarg, parent, ctes)
            return left
        scope = _Scope2(parent, ctes)
        if sel.valuesLists:
            for row in _iter_nodes(sel.valuesLists):
                self.walk(row, scope, set())
            return Entry(set(), True)
        for item in sel.fromClause or ():
            self.add_from(item, scope)
        aliases = {rt.name for rt in (sel.targetList or ()) if isinstance(rt, A.ResTarget) and rt.name}
        for rt in sel.targetList or ():
            self.walk(rt, scope, set())
        self.walk(sel.whereClause, scope, set())
        for q in scope.quals:
            self.walk(q, scope, set())
        self.walk(sel.groupClause, scope, aliases)
        self.walk(sel.havingClause, scope, set())
        self.walk(sel.windowClause, scope, set())
        self.walk(sel.distinctClause, scope, aliases)
        self.walk(sel.sortClause, scope, aliases)
        self.walk(sel.limitCount, scope, set())
        self.walk(sel.limitOffset, scope, set())
        return self.output_columns(sel, scope)

    def output_columns(self, sel, scope) -> Entry:
        cols, open_ = set(), False
        for rt in sel.targetList or ():
            if not isinstance(rt, A.ResTarget):
                open_ = True
                continue
            if rt.name:
                cols.add(rt.name)
                continue
            v = rt.val
            while isinstance(v, A.TypeCast):
                v = v.arg
            if isinstance(v, A.ColumnRef):
                last = v.fields[-1]
                if isinstance(last, A.A_Star):
                    if len(v.fields) == 1:
                        ents = list(scope.entries.values())
                    else:
                        q = v.fields[-2].sval
                        ents = [scope.entries[q]] if q in scope.entries else []
                    if not ents:
                        open_ = True
                    for e in ents:
                        cols |= e.cols
                        open_ = open_ or e.open
                else:
                    cols.add(last.sval)
            elif isinstance(v, A.FuncCall):
                cols.add(v.funcname[-1].sval)
            elif isinstance(v, A.CoalesceExpr):
                cols.add("coalesce")
            elif isinstance(v, A.CaseExpr):
                cols.add("case")
            else:
                open_ = True
        return Entry(cols, open_)

    # ---- dml
    def _target(self, rel: A.RangeVar, ctes):
        ent = self.table_entry(rel, ctes)
        name = rel.alias.aliasname if rel.alias else rel.relname
        return name, ent

    def _check_target_cols(self, names, ent: Entry, where):
        if ent.open or ent.table is None:
            return
        for n in names:
            if n and not n.startswith(PH) and n not in ent.cols:
                self.report("UNKNOWN_COLUMN", "error", f"{where}: no column '{n}' in table '{ent.table}'")

    def analyze_dml(self, st, parent, ctes):
        ctes = dict(ctes)
        if st.withClause is not None:
            for cte in st.withClause.ctes:
                if st.withClause.recursive:
                    ctes[cte.ctename] = Entry(set(), True)
                ent = self.analyze_query(cte.ctequery, parent, ctes)
                if cte.aliascolnames:
                    ent = Entry({c.sval for c in cte.aliascolnames}, True)
                ctes[cte.ctename] = ent
        alias, ent = self._target(st.relation, ctes)
        scope = _Scope2(parent, ctes)
        scope.entries[alias] = ent
        if isinstance(st, A.InsertStmt):
            self._check_target_cols([rt.name for rt in (st.cols or ())], ent, f"INSERT INTO {st.relation.relname}")
            if isinstance(st.selectStmt, A.SelectStmt):
                self.analyze_select(st.selectStmt, parent, ctes)
            oc = st.onConflictClause
            if oc is not None:
                if oc.infer is not None:
                    self._check_target_cols([ie.name for ie in (oc.infer.indexElems or ()) if isinstance(ie, A.IndexElem)],
                                            ent, f"ON CONFLICT ({st.relation.relname})")
                self._check_target_cols([rt.name for rt in (oc.targetList or ())], ent,
                                        f"ON CONFLICT DO UPDATE SET {st.relation.relname}")
                oscope = _Scope2(parent, ctes)
                oscope.entries[alias] = ent
                oscope.entries["excluded"] = ent
                for rt in oc.targetList or ():
                    self.walk(rt, oscope, set())
                self.walk(oc.whereClause, oscope, set())
                if oc.infer is not None:
                    self.walk(oc.infer.whereClause, oscope, set())
        elif isinstance(st, A.UpdateStmt):
            self._check_target_cols([rt.name for rt in (st.targetList or ())], ent, f"UPDATE {st.relation.relname} SET")
            for item in st.fromClause or ():
                self.add_from(item, scope)
            for rt in st.targetList or ():
                self.walk(rt, scope, set())
            self.walk(st.whereClause, scope, set())
            for q in scope.quals:
                self.walk(q, scope, set())
        else:  # delete
            for item in st.usingClause or ():
                self.add_from(item, scope)
            self.walk(st.whereClause, scope, set())
            for q in scope.quals:
                self.walk(q, scope, set())
        ret = getattr(st, "returningList", None) or getattr(st, "returningClause", None)
        if ret is not None and not isinstance(ret, A.ReturningClause if hasattr(A, "ReturningClause") else ()):
            self.walk(ret, scope, set())
        elif ret is not None:
            self.walk(getattr(ret, "exprs", None), scope, set())

    # ---- expressions
    def walk(self, node, scope, aliases):
        if node is None:
            return
        if isinstance(node, (tuple, list)):
            for n in node:
                self.walk(n, scope, aliases)
            return
        if isinstance(node, A.ColumnRef):
            self.check_colref(node, scope, aliases)
            return
        if isinstance(node, A.SelectStmt):
            self.analyze_select(node, scope, scope.ctes)
            return
        if isinstance(node, A.ResTarget):
            self.walk(node.val, scope, aliases)
            return
        if isinstance(node, (A.TypeName, A.A_Const)):
            return
        for ch in _children(node):
            self.walk(ch, scope, aliases)

    def check_colref(self, cr: A.ColumnRef, scope: _Scope2, aliases):
        fields = cr.fields
        if not all(isinstance(f, A.String) for f in fields):
            return  # contains `*`
        names = [f.sval for f in fields]
        if any(n.startswith(PH) for n in names):
            return
        if len(names) == 1:
            col = names[0]
            if self.approx or col in aliases or col.lower() in _SYSCOLS:
                return
            any_entry = False
            for sc in scope.chain():
                for key, ent in sc.entries.items():
                    any_entry = True
                    if ent.open or col in ent.cols or col == key:
                        return
            if any_entry:
                tbls = sorted({e.table or k for sc in scope.chain() for k, e in sc.entries.items()})
                self.report("UNKNOWN_COLUMN", "error", f"column '{col}' not found in {', '.join(tbls)}")
            return
        qual, col = names[-2], names[-1]
        ent = None
        for sc in scope.chain():
            if qual in sc.entries:
                ent = sc.entries[qual]
                break
        if ent is None:
            if not self.approx:
                self.report("UNKNOWN_ALIAS", "error", f"'{qual}.{col}': '{qual}' is not a table/alias in scope")
            return
        if ent.open or col in ent.cols or col.lower() in _SYSCOLS:
            return
        self.report("UNKNOWN_COLUMN", "error", f"'{qual}.{col}': no column '{col}' in {ent.table or qual}")


# ------------------------------------------------------------------------------------------------
# Part 4: per-statement checks
# ------------------------------------------------------------------------------------------------

_PARSE_ERR_POS = re.compile(r"at index (\d+)")
_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _try_parse(tokens):
    """Try placeholder candidates until the statement parses.
    Returns dict(ok, tree, pg_text, raw, reliable, bad, pos, names, err, err_pos)."""
    ctxs = _contexts(tokens)
    n = len(ctxs)
    attempts = [[0] * n]
    if n:
        for i in range(n):
            for j in range(1, len(ctxs[i][0])):
                c = [0] * n
                c[i] = j
                attempts.append(c)
        for j in range(1, max(len(c[0]) for c in ctxs)):
            attempts.append([min(j, len(c[0]) - 1) for c in ctxs])
        for i in range(n):
            for k in range(i + 1, n):
                for j in range(1, len(ctxs[i][0])):
                    for l in range(1, len(ctxs[k][0])):
                        c = [0] * n
                        c[i], c[k] = j, l
                        attempts.append(c)
    first_err = None
    for ch in attempts[:MAX_PARSE_ATTEMPTS]:
        raw, reliable = render(tokens, ch)
        pg_text, pos, names, bad = convert_params(raw)
        try:
            tree = pglast.parse_sql(pg_text)
        except pglast.parser.ParseError as e:
            if first_err is None:
                m = _PARSE_ERR_POS.search(str(e))
                first_err = dict(ok=False, tree=None, pg_text=pg_text, raw=raw, reliable=reliable, bad=bad,
                                 pos=pos, names=names, err=str(e), err_pos=int(m.group(1)) if m else 0)
            continue
        return dict(ok=True, tree=tree, pg_text=pg_text, raw=raw, reliable=reliable, bad=bad, pos=pos,
                    names=names, err=None, err_pos=0)
    return first_err


def _subsets(opt_count):
    import itertools
    idx = list(range(opt_count))
    if opt_count <= 6:
        for r in range(0, opt_count + 1):
            for c in itertools.combinations(idx, r):
                yield set(c)
    else:
        yield set()
        for i in idx:
            yield {i}


def _tokens_without(var: Var, keep: set):
    """Tokens of `var` with the `+=` pieces not in `keep` removed (pieces sit at the end, in order)."""
    n_opt_tokens = sum(len(o) for o in var.optional)
    base = var.tokens[: len(var.tokens) - n_opt_tokens]
    out = list(base)
    for i, o in enumerate(var.optional):
        if i in keep:
            out += o
    return out


def check_statement(stmt: SqlStatement, schema: dict, seen_tables: set | None = None) -> list[Finding]:
    seen_tables = seen_tables if seen_tables is not None else set()
    findings: list[Finding] = []
    approx = stmt.approximate
    var = stmt.var
    res = _try_parse(var.tokens)
    parsed = [res] if res and res["ok"] else []
    if not parsed and var.optional:
        for keep in _subsets(len(var.optional)):
            r = _try_parse(_tokens_without(var, keep))
            if r and r["ok"]:
                parsed.append(r)
                if len(parsed) >= 8:
                    break
    ref = parsed[0] if parsed else res
    sql_snip = _norm(ref["raw"], 300) if ref else ""

    def add(code, sev, msg, line=None):
        f = Finding(stmt.file, line or stmt.line, code, sev, msg, sql_snip, approx)
        findings.append(f)

    if not parsed:
        pg = ref["pg_text"]
        i = ref["err_pos"]
        snippet = _norm(pg[max(0, i - 40): i + 40], 90)
        line = stmt.line + pg[:i].count("\n")
        msg = f"{ref['err'].split(', at index')[0]} near: ...{snippet}..."
        add("SYNTAX_ERROR", "warn" if approx else "error", msg, line)
    # -- parameter checks (on the first rendering we have)
    p = stmt.params
    if ref and ref["reliable"] and not var.optional:
        npos, names = ref["pos"], ref["names"]
        if p.kind == "seq":
            if names:
                add("PARAM_COUNT_MISMATCH", "error", f"named placeholders {list(names)} but a tuple/list is passed", stmt.call_line)
            elif npos != p.count:
                add("PARAM_COUNT_MISMATCH", "error", f"{npos} placeholder(s) in SQL but {p.count} parameter(s) passed", stmt.call_line)
        elif p.kind == "dict":
            if npos:
                add("PARAM_COUNT_MISMATCH", "error", f"{npos} positional placeholder(s) but a dict is passed", stmt.call_line)
            missing = [n for n in names if n not in p.names]
            if missing:
                add("PARAM_COUNT_MISMATCH", "error", f"placeholder name(s) {missing} missing from the params dict", stmt.call_line)
        elif p.kind == "none" and (npos or names):
            add("PARAM_COUNT_MISMATCH", "error", f"{npos + len(names)} placeholder(s) in SQL but no parameters passed", stmt.call_line)
    if ref and ref["bad"] and p.kind != "none" and not var.optional:
        txt = ref["raw"]
        j = ref["bad"][0]
        sev = "error" if p.kind in ("seq", "dict") and (p.count or p.kind == "dict") else "warn"
        if p.kind in ("seq", "dict", "unknown"):
            add("BAD_PERCENT", sev,
                "a lone '%' (use '%%') in SQL that is executed with parameters: psycopg2 raises "
                f"'unsupported format character' near: ...{_norm(txt[max(0, j - 30): j + 30], 70)}...",
                stmt.call_line)
    if ref and p.kind != "none":
        for lit in _LITERAL.findall(ref["raw"]):
            if re.search(r"%s", lit) and "%%" in lit:
                add("PLACEHOLDER_IN_QUOTES", "warn",
                    f"%s inside a quoted pattern {_norm(lit, 50)}: psycopg2 would quote the value inside the quotes "
                    "(build the pattern in Python and pass it as the parameter)", stmt.call_line)
                break
    # -- schema checks
    seen_msgs = set()

    def report(code, sev, msg):
        if (code, msg) in seen_msgs:
            return
        seen_msgs.add((code, msg))
        findings.append(Finding(stmt.file, stmt.line, code, sev, msg, sql_snip, approx))

    for r in parsed:
        an = Analyzer(schema, approx, seen_tables, report)
        for rs in r["tree"]:
            an.analyze(rs.stmt)
    return findings


def check_source(source: str, schema: dict, filename: str = "<src>", seen_tables: set | None = None):
    """Returns (statements_checked, findings) for one Python source text."""
    stmts = extract_statements(source, filename)
    seen = seen_tables if seen_tables is not None else set()
    out: list[Finding] = []
    for st in stmts:
        out += check_statement(st, schema, seen)
    # de-duplicate (a statement variant can be reached through several calls)
    uniq, seen_f = [], set()
    for f in out:
        k = (f.file, f.line, f.code, f.message)
        if k not in seen_f:
            seen_f.add(k)
            uniq.append(f)
    return len(stmts), uniq


# ------------------------------------------------------------------------------------------------
# Part 5: CLI
# ------------------------------------------------------------------------------------------------


def collect_files(paths) -> list[Path]:
    files: list[Path] = []
    if not paths:
        for g in SCAN_GLOBS:
            files += sorted(BE_ROOT.glob(g))
    else:
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                files += sorted(x for x in pp.rglob("*.py") if "__pycache__" not in x.parts)
            elif pp.exists():
                files.append(pp)
    seen, out = set(), []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(f)
    return out


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(BE_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def assign_keys(findings: list[Finding]):
    counts: dict[str, int] = {}
    for f in findings:
        base = finding_key(f)
        counts[base] = counts.get(base, 0) + 1
        f.key = f"{base}#{counts[base]}"


def run_check(paths=None, ddl: Path = DEFAULT_DDL):
    schema = load_schema(Path(ddl).read_text(encoding="utf-8-sig"))
    total, findings = 0, []
    seen_tables_by_file: dict[str, set] = {}
    for f in collect_files(paths):
        rel = _rel(f)
        n, fs = check_source(f.read_text(encoding="utf-8-sig"), schema, rel, seen_tables_by_file.setdefault(rel, set()))
        total += n
        findings += fs
    findings.sort(key=lambda x: (x.file, x.line, x.code))
    assign_keys(findings)
    return total, findings


def load_baseline(path) -> set:
    if not path or not Path(path).exists():
        return set()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {f["key"] for f in data.get("findings", [])}


def write_baseline(path, findings):
    data = {
        "comment": "Findings accepted as pre-existing by scripts/sql_static_check.py. Keys do not contain line "
                   "numbers. Regenerate only after triage: python scripts/sql_static_check.py "
                   "--write-baseline scripts/sql_static_baseline.json",
        "findings": [f.to_dict() for f in findings],
    }
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Static SQL checker (pglast + DDL aware)")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--paths", nargs="*", default=None, help="files/directories (default: whole backend)")
    ap.add_argument("--baseline", default=None, help="baseline json; only findings not in it fail the run")
    ap.add_argument("--no-baseline", action="store_true", help="ignore the default baseline file")
    ap.add_argument("--write-baseline", default=None, help="write all current findings to this file and exit 0")
    ap.add_argument("--ddl", default=str(DEFAULT_DDL), help="schema file (default punchapp_ddl_latest.sql)")
    ap.add_argument("--all", action="store_true", help="also print baselined findings")
    args = ap.parse_args(argv)

    total, findings = run_check(args.paths, Path(args.ddl))
    if args.write_baseline:
        write_baseline(args.write_baseline, findings)
        print(f"baseline written: {len(findings)} findings, {total} statements -> {args.write_baseline}")
        return 0
    base_path = args.baseline
    if base_path is None and not args.no_baseline and DEFAULT_BASELINE.exists():
        base_path = str(DEFAULT_BASELINE)
    baseline = load_baseline(base_path)
    new = [f for f in findings if f.key not in baseline]
    new_errors = [f for f in new if f.severity == "error"]
    shown = findings if args.all else new
    summary = {
        "statements": total, "findings": len(findings),
        "errors": sum(f.severity == "error" for f in findings),
        "warnings": sum(f.severity == "warn" for f in findings),
        "baselined": len(findings) - len(new), "new": len(new), "new_errors": len(new_errors),
    }
    if args.json:
        print(json.dumps({"summary": summary, "findings": [f.to_dict() for f in shown]}, indent=1))
    else:
        cur = None
        for f in shown:
            if f.file != cur:
                cur = f.file
                print(f"\n{cur}")
            tag = "" if f.key not in baseline else " [baselined]"
            print(f"  {f.line}: {f.severity.upper():5} {f.code}{' (approx)' if f.approximate else ''}{tag}: {f.message}")
            if f.sql and f.severity == "error":
                print(f"         sql: {f.sql[:160]}")
        print(f"\nstatements checked: {total}; findings: {summary['findings']} "
              f"({summary['errors']} errors, {summary['warnings']} warnings); "
              f"baselined: {summary['baselined']}; new: {summary['new']} ({summary['new_errors']} errors)")
    return 1 if new_errors else 0


if __name__ == "__main__":
    sys.exit(main())
