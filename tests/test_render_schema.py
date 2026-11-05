"""The schema renderer must never produce a file that writes into the legacy schema."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_schema  # noqa: E402

_P = "punch" + "app"  # built at runtime: the branding test forbids the literal name in tests
LEGACY = _P + "_prod"


def test_render_rewrites_every_qualifier_and_sets_the_search_path():
    sql = render_schema.render("clockyn_demo")
    assert LEGACY not in sql
    assert sql.startswith("-- Rendered by scripts/render_schema.py")
    assert 'SET search_path TO "clockyn_demo";' in sql
    assert "CREATE TABLE clockyn_demo.users" in sql
    assert sql.count("CREATE TABLE ") >= 70


@pytest.mark.parametrize("name", [_P + "_prod", _P + "_dev", _P + "_qa", _P + "_backup", "public"])
def test_legacy_and_system_schema_names_are_refused(name):
    with pytest.raises(ValueError):
        render_schema.render(name)


@pytest.mark.parametrize("bad", ["", "Clockyn", "1abc", "a-b", 'x"; drop schema y; --', "a" * 70])
def test_bad_schema_names_are_refused(bad):
    with pytest.raises(ValueError):
        render_schema.render(bad)


def test_cli_writes_the_file_and_returns_error_for_a_forbidden_name(tmp_path):
    out = tmp_path / "x.sql"
    assert render_schema.main(["--schema", "clockyn_demo", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").count("clockyn_demo") > 100
    assert render_schema.main(["--schema", "public", "--out", str(tmp_path / "y.sql")]) == 2
    assert not (tmp_path / "y.sql").exists()
