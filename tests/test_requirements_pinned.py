"""Every line of requirements.txt must be `name==version` so an unpinned dependency cannot slip back in.

Also covers the parser behind scripts/dependency_report.py. DB-free, no network.
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, "requirements.txt")

# Deliberate exceptions (name -> reason). Keep this empty unless a pin is technically impossible.
ALLOWED_UNPINNED = {}


def _load_report_module():
    path = os.path.join(ROOT, "scripts", "dependency_report.py")
    spec = importlib.util.spec_from_file_location("dependency_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load_report_module()


def test_every_requirement_is_pinned_exactly():
    rows = dr.load(REQ)
    assert rows, "requirements.txt has no requirements?"
    bad = [r["raw"] for r in rows if not r["pinned"] and r["name"].lower() not in ALLOWED_UNPINNED]
    assert not bad, "unpinned requirement(s) (use name==version): %s" % bad


def test_no_duplicate_requirement_names():
    names = [r["name"].lower().replace("_", "-") for r in dr.load(REQ)]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, "duplicate requirement(s): %s" % dupes


def test_packaging_is_pinned():
    # regression: `packaging` was the single floating direct dependency
    by_name = {r["name"].lower(): r for r in dr.load(REQ)}
    assert by_name["packaging"]["pinned"] and by_name["packaging"]["version"]


def test_parser_accepts_pins_extras_comments_and_markers():
    rows = dr.parse_requirements(
        "# comment\n\nfastapi==0.128.6\r\nuvicorn[standard]==0.24.0  # trailing\nfoo==1.0 ; python_version < '3.12'\n"
    )
    assert [(r["name"], r["version"], r["pinned"]) for r in rows] == [
        ("fastapi", "0.128.6", True), ("uvicorn", "0.24.0", True), ("foo", "1.0", True)]


def test_parser_flags_unpinned_forms():
    rows = dr.parse_requirements(
        "packaging\nrequests>=2\nfoo~=1.2\nbar==1.*\nbaz<3\n-r other.txt\ngit+https://x/y.git\n"
    )
    assert len(rows) == 7
    assert not any(r["pinned"] for r in rows)
    assert [r["name"] for r in rows][:5] == ["packaging", "requests", "foo", "bar", "baz"]


def test_report_and_exit_code(tmp_path, capsys):
    good = tmp_path / "good.txt"
    good.write_text("a==1\nb==2\n")
    assert dr.main([str(good)]) == 0
    bad = tmp_path / "bad.txt"
    bad.write_text("a==1\nb\n")
    assert dr.main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert "UNPINNED" in out and "2 requirement(s), 1 pinned, 1 unpinned" in out
