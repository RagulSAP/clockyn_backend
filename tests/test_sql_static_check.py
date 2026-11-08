"""DB-free tests for scripts/sql_static_check.py (the static SQL checker).
Run: python -m pytest tests/test_sql_static_check.py -q
"""
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import sql_static_check as S  # noqa: E402

DDL = """
CREATE SCHEMA clockyn_dev;
CREATE TABLE clockyn_dev.users (user_id varchar(36), name text, email text, org_id varchar(36), status int4);
CREATE TABLE clockyn_dev.org_info (org_id varchar(36), org_name text);
CREATE TABLE clockyn_dev.requests (id int4, user_id varchar(36), kind text);
CREATE UNIQUE INDEX ux ON clockyn_dev.users (user_id);
"""
SCHEMA = S.load_schema(DDL)


def code(body: str) -> str:
    """A tiny module with one function whose body is `body` (cursor is `cur`)."""
    return "def f(cur, a, b):\n" + textwrap.indent(textwrap.dedent(body).strip("\n"), "    ") + "\n"


def run(body: str):
    n, fs = S.check_source(code(body), SCHEMA, "t.py")
    return n, fs


def codes(body: str):
    return sorted((f.code, f.severity) for f in run(body)[1])


def stmts(body: str):
    return S.extract_statements(code(body), "t.py")


# ---------------------------------------------------------------- schema loading
def test_load_schema_strips_qualifier_and_ignores_indexes():
    assert SCHEMA["users"] == {"user_id", "name", "email", "org_id", "status"}
    assert set(SCHEMA) == {"users", "org_info", "requests"}


def test_load_schema_alter_add_drop_column():
    sch = S.load_schema(
        "CREATE TABLE s.t (a int, b int); ALTER TABLE s.t ADD COLUMN c int; ALTER TABLE s.t DROP COLUMN b;")
    assert sch["t"] == {"a", "c"}


# ---------------------------------------------------------------- extractor
def test_extract_constant_implicit_concat_and_triple_quote():
    sts = stmts('''
        cur.execute("SELECT name "
                    "FROM users WHERE user_id = %s", (a,))
        cur.execute("""
            SELECT email FROM users
            WHERE user_id = %s
        """, (a,))
    ''')
    assert len(sts) == 2
    assert [s.approximate for s in sts] == [False, False]
    assert " ".join(sts[0].sql.split()) == "SELECT name FROM users WHERE user_id = %s"
    assert "SELECT email FROM users" in sts[1].sql
    assert sts[0].params.kind == "seq" and sts[0].params.count == 1


def test_extract_fstring_placeholders_are_approximate():
    sts = stmts('''
        table_name = a.table
        cur.execute(f"SELECT * FROM {table_name} WHERE {a.field} = %s", (b,))
        cur.execute(f"SELECT * FROM users WHERE {make_clause(a)} AND status = 1")
    ''')
    assert all(s.approximate for s in sts)
    assert sts[0].sql == "SELECT * FROM x_placeholder WHERE x_placeholder = %s"
    assert sts[1].sql == "SELECT * FROM users WHERE 1 = 1 AND status = 1"


def test_extract_fstring_with_resolvable_variable_is_exact():
    sts = stmts('''
        cols = "user_id, name"
        cur.execute(f"SELECT {cols} FROM users")
    ''')
    assert len(sts) == 1 and not sts[0].approximate
    assert sts[0].sql == "SELECT user_id, name FROM users"


def test_extract_query_plus_equals_accumulates():
    sts = stmts('''
        query = "SELECT user_id FROM users WHERE 1=1"
        params = []
        if a:
            query += " AND name = %s"
            params.append(a)
        query += " ORDER BY name"
        cur.execute(query, params)
    ''')
    assert len(sts) == 1
    assert sts[0].sql == "SELECT user_id FROM users WHERE 1=1 AND name = %s ORDER BY name"
    assert len(sts[0].var.optional) == 2


def test_extract_branch_assignments_do_not_mix():
    # the 'else' arm returns, so the final execute only sees the if/elif arms
    sts = stmts('''
        if a:
            q = "SELECT user_id FROM users WHERE status = %s"
        else:
            q = "SELECT name FROM users"
            cur.execute(q)
            return
        cur.execute(q, (b,))
    ''')
    texts = sorted(s.sql for s in sts)
    assert texts == ["SELECT name FROM users", "SELECT user_id FROM users WHERE status = %s"]
    _, fs = run('''
        if a:
            q = "SELECT user_id FROM users WHERE status = %s"
        else:
            q = "SELECT name FROM users"
            cur.execute(q)
            return
        cur.execute(q, (b,))
    ''')
    assert fs == []


def test_extract_format_and_plus_and_strip():
    sts = stmts('''
        cur.execute("SELECT * FROM {} WHERE user_id = %s".format(a), (b,))
        cur.execute("SELECT user_id FROM users " + a)
        cur.execute("  SELECT 1 ".strip())
    ''')
    assert [s.approximate for s in sts] == [True, True, False]
    assert sts[0].sql == "SELECT * FROM x_placeholder WHERE user_id = %s"


def test_non_sql_execute_is_ignored():
    assert stmts('''
        executor.execute("hello world")
        cur.execute(a)
    ''') == []


# ---------------------------------------------------------------- placeholder conversion
@pytest.mark.parametrize("raw, pg, npos, names, nbad", [
    ("SELECT %s, %s", "SELECT $1, $2", 2, (), 0),
    ("SELECT '100%%' WHERE a = %s", "SELECT '100%' WHERE a = $1", 1, (), 0),
    ("WHERE a = %(x)s OR b = %(y)s OR c = %(x)s", "WHERE a = $1 OR b = $2 OR c = $1", 0, ("x", "y"), 0),
    ("WHERE a IN %s", "WHERE a IN ($1)", 1, (), 0),
    ("WHERE a NOT IN %s AND b = ANY(%s)", "WHERE a NOT IN ($1) AND b = ANY($2)", 2, (), 0),
    ("INSERT INTO t VALUES %s", "INSERT INTO t VALUES ($1)", 1, (), 0),
    ("WHERE ts > now() - INTERVAL %s", "WHERE ts > now() - ($1)::interval", 1, (), 0),
    ("WHERE name LIKE 'a%' AND x = %s", "WHERE name LIKE 'a%' AND x = $1", 1, (), 1),
    ("WHERE a % 2 = 0", "WHERE a % 2 = 0", 0, (), 1),
])
def test_convert_params(raw, pg, npos, names, nbad):
    out, pos, nm, bad = S.convert_params(raw)
    assert out == pg
    assert pos == npos
    assert nm == names
    assert len(bad) == nbad


# ---------------------------------------------------------------- param counting
def test_param_count_ok_and_mismatch():
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %s AND name = %s", (a, b))') == []
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %s", (a, b))') == [("PARAM_COUNT_MISMATCH", "error")]
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %s AND name = %s", [a])') == [("PARAM_COUNT_MISMATCH", "error")]
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %s")') == [("PARAM_COUNT_MISMATCH", "error")]
    assert codes('cur.execute("SELECT 1 FROM users", (a,))') == [("PARAM_COUNT_MISMATCH", "error")]


def test_param_count_percent_percent_is_not_a_placeholder():
    assert codes("cur.execute(\"SELECT 1 FROM users WHERE name LIKE 'a%%' AND user_id = %s\", (a,))") == []


def test_param_count_named_and_dict():
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %(u)s", {"u": a})') == []
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %(u)s", {"v": a})') == [("PARAM_COUNT_MISMATCH", "error")]
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %(u)s", (a,))') == [("PARAM_COUNT_MISMATCH", "error")]


def test_param_count_skipped_when_params_not_literal_or_sql_conditional():
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id = %s", params)') == []
    assert codes('''
        q = "SELECT 1 FROM users WHERE 1=1"
        if a:
            q += " AND user_id = %s"
        cur.execute(q, (a, b))
    ''') == []


def test_param_count_f_string_identifier_placeholder_still_counted():
    assert codes('cur.execute(f"SELECT 1 FROM {a} WHERE user_id = %s", (a, b))') == [("PARAM_COUNT_MISMATCH", "error")]
    # {clause} may contain its own %s, so the count is not trusted
    assert codes('cur.execute(f"SELECT 1 FROM users WHERE {a} AND user_id = %s", (a, b))') == []


def test_in_percent_s_tuple_idiom_is_valid():
    assert codes('cur.execute("SELECT 1 FROM users WHERE user_id IN %s", (tuple(a),))') == []


def test_bare_percent_with_params_is_reported():
    assert codes("cur.execute(\"SELECT 1 FROM users WHERE name LIKE 'a%' AND user_id = %s\", (a,))") == [("BAD_PERCENT", "error")]


# ---------------------------------------------------------------- syntax
def test_syntax_error_reported_with_snippet():
    n, fs = run('cur.execute("SELECT user_id FORM users WHERE user_id = %s", (a,))')
    assert [f.code for f in fs] == ["SYNTAX_ERROR"]
    assert fs[0].severity == "error" and fs[0].line == 2
    assert "FORM" in fs[0].message or "users" in fs[0].message


def test_syntax_error_in_unresolved_fstring_is_only_a_warning():
    n, fs = run('cur.execute(f"SELECT FROM WHERE {a} ((")')
    assert [(f.code, f.severity) for f in fs] == [("SYNTAX_ERROR", "warn")]


def test_multiline_syntax_error_line_points_into_the_string():
    n, fs = run('''
        cur.execute("""
            SELECT user_id
            FROM users
            WHERE ,
        """)
    ''')
    assert [f.code for f in fs] == ["SYNTAX_ERROR"]
    assert fs[0].line == 2 + 3  # def line 1; the call starts on line 2; the bad token is 3 lines below


# ---------------------------------------------------------------- columns
def test_unknown_qualified_column_and_alias():
    assert codes('cur.execute("SELECT u.emailx FROM users u")') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("SELECT u.email, o.org_name FROM users u JOIN org_info o ON o.org_id = u.org_id")') == []
    assert codes('cur.execute("SELECT x.email FROM users u")') == [("UNKNOWN_ALIAS", "error")]
    # table name used as qualifier when there is no alias
    assert codes('cur.execute("SELECT users.email FROM users")') == []
    assert codes('cur.execute("SELECT clockyn_dev.users.email FROM clockyn_dev.users")') == []


def test_unknown_unqualified_column():
    assert codes('cur.execute("SELECT nme FROM users WHERE user_id = %s", (a,))') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("SELECT name FROM users WHERE user_id = %s", (a,))') == []
    # join: found in either table
    assert codes('cur.execute("SELECT name, org_name FROM users u JOIN org_info o ON o.org_id = u.org_id")') == []


def test_output_alias_allowed_in_order_by_not_in_where():
    assert codes('cur.execute("SELECT name AS n FROM users ORDER BY n")') == []
    assert codes('cur.execute("SELECT name AS n FROM users WHERE n = 1")') == [("UNKNOWN_COLUMN", "error")]


def test_insert_and_update_target_columns():
    assert codes('cur.execute("INSERT INTO users (user_id, foo) VALUES (%s, %s)", (a, b))') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("INSERT INTO users (user_id, name) VALUES (%s, %s)", (a, b))') == []
    assert codes('cur.execute("UPDATE users SET bar = %s WHERE user_id = %s", (a, b))') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("UPDATE users SET name = %s WHERE user_id = %s AND status = 1", (a, b))') == []
    assert codes('cur.execute("DELETE FROM users WHERE nope = %s", (a,))') == [("UNKNOWN_COLUMN", "error")]


def test_on_conflict_excluded_and_returning():
    sql = ("INSERT INTO users (user_id, name) VALUES (%s, %s) "
           "ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name RETURNING user_id")
    assert codes(f'cur.execute("{sql}", (a, b))') == []
    bad = sql.replace("ON CONFLICT (user_id)", "ON CONFLICT (uid)")
    assert codes(f'cur.execute("{bad}", (a, b))') == [("UNKNOWN_COLUMN", "error")]
    bad2 = sql.replace("EXCLUDED.name", "EXCLUDED.nam")
    assert codes(f'cur.execute("{bad2}", (a, b))') == [("UNKNOWN_COLUMN", "error")]


def test_update_from_and_delete_using():
    assert codes('cur.execute("UPDATE users u SET name = o.org_name FROM org_info o WHERE o.org_id = u.org_id")') == []
    assert codes('cur.execute("UPDATE users u SET name = o.nope FROM org_info o WHERE o.org_id = u.org_id")') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("DELETE FROM users u USING org_info o WHERE o.org_id = u.org_id AND o.org_name = %s", (a,))') == []


def test_cte_subquery_and_derived_columns():
    assert codes('''cur.execute("""
        WITH t AS (SELECT user_id, name AS nm FROM users)
        SELECT t.nm, r.kind FROM t JOIN requests r ON r.user_id = t.user_id
    """)''') == []
    assert codes('''cur.execute("""
        WITH t AS (SELECT user_id FROM users)
        SELECT t.nm FROM t
    """)''') == [("UNKNOWN_COLUMN", "error")]
    assert codes('cur.execute("SELECT s.c FROM (SELECT count(*) AS c FROM users) s")') == []
    assert codes('cur.execute("SELECT s.zzz FROM (SELECT count(*) AS c FROM users) s")') == [("UNKNOWN_COLUMN", "error")]
    # SELECT * of a known table is expanded
    assert codes('cur.execute("SELECT s.name FROM (SELECT * FROM users) s")') == []
    assert codes('cur.execute("SELECT s.zzz FROM (SELECT * FROM users) s")') == [("UNKNOWN_COLUMN", "error")]


def test_correlated_subquery_sees_outer_alias():
    assert codes('''cur.execute("""
        SELECT u.user_id, (SELECT count(*) FROM requests r WHERE r.user_id = u.user_id) AS n
        FROM users u
    """)''') == []
    assert codes('''cur.execute("""
        SELECT u.user_id FROM users u WHERE EXISTS (SELECT 1 FROM requests r WHERE r.user_id = u.user_idx)
    """)''') == [("UNKNOWN_COLUMN", "error")]


def test_set_returning_function_and_values_are_open():
    assert codes('cur.execute("SELECT g.d FROM generate_series(1, 3) AS g(d)")') == []
    assert codes('cur.execute("SELECT g.whatever FROM generate_series(1, 3) AS g")') == []
    assert codes('cur.execute("SELECT v.a FROM (VALUES (1), (2)) AS v(a)")') == []


def test_union_and_system_columns():
    assert codes('cur.execute("SELECT name FROM users UNION ALL SELECT org_name FROM org_info ORDER BY 1")') == []
    assert codes('cur.execute("SELECT ctid FROM users")') == []


def test_unknown_table_is_a_single_warning_per_file():
    n, fs = run('''
        cur.execute("SELECT * FROM ghosts WHERE a = 1")
        cur.execute("SELECT * FROM ghosts g WHERE g.a = 1")
        cur.execute("SELECT * FROM pg_catalog.pg_tables")
    ''')
    assert [(f.code, f.severity) for f in fs] == [("UNKNOWN_TABLE", "warn")]


def test_fstring_placeholders_do_not_produce_false_positives():
    assert codes('cur.execute(f"UPDATE users SET {a} WHERE user_id = %s", (b,))') == []
    assert codes('cur.execute(f"INSERT INTO users ({a}) VALUES ({b})", (a,))') == []
    assert codes('cur.execute(f"SELECT {a}.name FROM users {a} WHERE {b}")') == []
    assert codes('cur.execute(f"SELECT u.email FROM users u {a} WHERE u.status = 1")') == []
    # qualified columns of a known table are still checked in an approximate statement
    assert codes('cur.execute(f"SELECT u.emailx FROM users u WHERE {a}")') == [("UNKNOWN_COLUMN", "error")]
    # unqualified columns are not checked in an approximate statement
    assert codes('cur.execute(f"SELECT nme FROM users WHERE {a}")') == []


def test_conditional_where_pieces_accept_if_any_combination_parses():
    assert codes('''
        if a:
            q = "SELECT name FROM users WHERE status = %s"
        q2 = "SELECT name FROM users"
        if a:
            q2 += " WHERE status = %s"
        elif b:
            q2 += " WHERE name = %s"
        cur.execute(q2)
    ''') == []


def test_unqualified_check_skipped_when_a_source_is_open():
    assert codes('cur.execute("SELECT whatever FROM users u, generate_series(1,2) g")') == []


# ---------------------------------------------------------------- baseline / CLI
def test_keys_do_not_depend_on_line_numbers():
    a = S.check_source(code('cur.execute("SELECT nme FROM users")'), SCHEMA, "t.py")[1]
    b = S.check_source("\n\n\n" + code('cur.execute("SELECT nme FROM users")'), SCHEMA, "t.py")[1]
    S.assign_keys(a)
    S.assign_keys(b)
    assert a[0].line != b[0].line and a[0].key == b[0].key


def test_cli_exit_code_with_baseline(tmp_path):
    ddl = tmp_path / "ddl.sql"
    ddl.write_text(DDL, encoding="utf-8")
    src = tmp_path / "mod.py"
    src.write_text(code('cur.execute("SELECT nme FROM users")'), encoding="utf-8")
    base = tmp_path / "base.json"
    args = ["--ddl", str(ddl), "--paths", str(src)]
    assert S.main(args + ["--no-baseline"]) == 1
    assert S.main(args + ["--write-baseline", str(base)]) == 0
    assert S.main(args + ["--baseline", str(base)]) == 0
    src.write_text(code('cur.execute("SELECT nme FROM users")\ncur.execute("SELECT u.nope FROM users u")'), encoding="utf-8")
    assert S.main(args + ["--baseline", str(base)]) == 1


def test_repo_has_no_new_sql_errors_against_baseline():
    """Safety net: the SQL in the backend must not introduce errors beyond scripts/sql_static_baseline.json."""
    baseline = ROOT / "scripts" / "sql_static_baseline.json"
    assert baseline.exists(), "run: python scripts/sql_static_check.py --write-baseline scripts/sql_static_baseline.json"
    assert S.main(["--baseline", str(baseline)]) == 0
