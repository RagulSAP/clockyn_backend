"""DB-free regression tests found by the live SQL check (scripts/sql_live_check.py) and unit tests of its helpers.

The live run itself is tests/test_sql_live.py (needs a database). Everything here runs without one.
Run: python -m pytest tests/test_sql_live_regressions.py -q
"""
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import sql_live_check as L  # noqa: E402
import sql_static_check as S  # noqa: E402
import pglast  # noqa: E402
from pglast import ast as A  # noqa: E402


def _statements(rel: str):
    return S.extract_statements((ROOT / rel).read_text(encoding="utf-8-sig"), rel)


def _plain_columns(node, out):
    """Dotted names of the column references of an expression that are NOT inside a function / aggregate call."""
    if isinstance(node, A.ColumnRef):
        out.add(".".join(f.sval for f in node.fields if isinstance(f, A.String)))
    elif isinstance(node, A.FuncCall):
        return
    elif isinstance(node, A.Node):
        for slot in node.__slots__:
            v = getattr(node, slot, None)
            if isinstance(v, (tuple, list)):
                for x in v:
                    _plain_columns(x, out)
            elif isinstance(v, A.Node):
                _plain_columns(v, out)


def _grouping_gaps(sql: str):
    """Selected plain columns missing from GROUP BY, per grouped SELECT of the statement."""
    gaps = []
    pg, _ = L.to_pg(sql)
    for rs in pglast.parse_sql(pg):
        sel = rs.stmt
        if not isinstance(sel, A.SelectStmt) or not sel.groupClause:
            continue
        grouped = set()
        for g in sel.groupClause:
            _plain_columns(g, grouped)
        wanted = set()
        for t in sel.targetList:
            _plain_columns(t.val, wanted)
        gaps.append(sorted(wanted - grouped))
    return gaps


@pytest.mark.parametrize("rel", ["core_functions/suggest_feature_functions.py", "core_functions/support_functions.py"])
def test_group_by_lists_every_selected_column(rel):
    """email_log and admin_subject_request have no primary key in the schema, so `GROUP BY r.id` does not make the
    other selected columns functionally dependent (PostgreSQL error 42803, which the support code then hid by falling
    back to a query without replies)."""
    checked = 0
    for st in _statements(rel):
        sql = S.render(st.var.tokens, None)[0]
        if " GROUP BY " not in " ".join(sql.split()):
            continue
        for gap in _grouping_gaps(sql):
            assert gap == [], f"{rel}:{st.line} selects {gap} but does not group by it"
            checked += 1
    assert checked >= 2


def test_meta_attribution_compares_status_with_an_integer():
    """users.status is an integer column; comparing it with 'active' fails with 22P02 on every call."""
    text = (ROOT / "router" / "attribution.py").read_text(encoding="utf-8")
    assert "u.status = 'active'" not in text
    assert "CASE WHEN u.status = 1 THEN u.user_id END" in text


# ---------------------------------------------------------------------------------------------------------------
# helpers of the live checker
# ---------------------------------------------------------------------------------------------------------------


def test_to_pg_numbers_every_placeholder_separately():
    pg, n = L.to_pg("SELECT a FROM t WHERE x = %(o)s AND y = %(o)s AND z IN %s AND d > NOW() - INTERVAL %s AND n LIKE '100%%'")
    assert n == 4
    assert pg == "SELECT a FROM t WHERE x = $1 AND y = $2 AND z IN ($3) AND d > NOW() - ($4)::interval AND n LIKE '100%'"


def test_to_pg_without_placeholders():
    assert L.to_pg("SELECT 1") == ("SELECT 1", 0)


def test_format_with_literal_arguments_is_resolved():
    src = textwrap.dedent('''
        T = "SELECT a FROM t {join} JOIN u ON u.id = t.id WHERE {cond}"
        def f(cur):
            sql = T.format(join="INNER", cond="u.k = %(k)s")
            cur.execute(sql, {"k": 1})
    ''')
    S._Resolver = L._FormatResolver
    try:
        (st,) = S.extract_statements(src, "t.py")
    finally:
        S._Resolver = L._BaseResolver
    assert not st.approximate
    assert "INNER JOIN u ON" in st.sql and "u.k = %(k)s" in st.sql and "{" not in st.sql


def test_staff_row_sql_is_extracted_with_both_join_variants():
    items, _ = L.collect_items([str(ROOT / "core_functions" / "shift_functions.py")])
    texts = [v for it in items for v in it.variants]
    assert any("INNER JOIN" in t and "uai.shift_id = $" in t for t in texts)
    assert any("LEFT JOIN" in t and "IS DISTINCT FROM $" in t for t in texts)
    assert not any("{join}" in t or "{cond}" in t for t in texts)


def test_collect_items_covers_constants_outside_execute():
    items, stats = L.collect_items()
    files = {i.file for i in items}
    assert "core_functions/data_lifecycle.py" in files  # DELETE statements held in a list
    assert len(items) > 700
    assert all(i.variants for i in items)


def test_baseline_file_is_valid_json():
    base = L.load_baseline()
    assert isinstance(base.get("untyped", []), list)
    assert isinstance(base.get("accepted_errors", []), list)
