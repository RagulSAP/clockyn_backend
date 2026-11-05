"""Every route the Clockyn app calls exists on the backend with the same method.

Uses the vendored call list `tests/data/frontend_calls.json` (works without the frontend repo). When the frontend repo is
present the live extraction is compared with it and a stale list only WARNS (regenerate with
`python scripts/check_frontend_contract.py --regenerate`).
Run: python -m pytest tests/test_frontend_contract.py -q
"""
import importlib.util
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_frontend_contract", ROOT / "scripts" / "check_frontend_contract.py")
cfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfc)


def test_vendored_list_is_not_empty_and_well_formed():
    calls = cfc.load_vendored()
    assert len(calls) > 150
    assert {c["method"] for c in calls} <= {"GET", "POST", "PUT", "DELETE", "PATCH"}
    assert all(c["path"] is None or c["path"].startswith("/") for c in calls)
    assert ("POST", "/auth/validate-otp") in {(c["method"], c["path"]) for c in calls}


def test_every_frontend_call_has_a_backend_route():
    no_route, mismatch, unresolved, stale = cfc.check(cfc.load_vendored())
    assert not no_route, "app calls without a backend route:\n  " + "\n  ".join(no_route)
    assert not mismatch, "app calls whose method the backend does not serve:\n  " + "\n  ".join(mismatch)
    assert not unresolved, "calls the extractor cannot read (add them to EXPANSIONS / DYNAMIC_CALLS):\n  " + "\n  ".join(unresolved)
    assert not stale, "KNOWN_ABSENT entries that now exist: " + ", ".join(stale)


def test_checker_flags_a_missing_route_and_a_wrong_method():
    calls = [
        dict(file="x.ts", line=1, method="GET", path="/no/such/route"),
        dict(file="x.ts", line=2, method="DELETE", path="/auth/generate-otp"),
        dict(file="x.ts", line=3, method="POST", path="/auth/generate-otp"),
        dict(file="x.ts", line=4, method="GET", path="/addstaff/{param}"),
    ]
    no_route, mismatch, _unresolved, _stale = cfc.check(calls)
    assert len(no_route) == 1 and "/no/such/route" in no_route[0]
    assert len(mismatch) == 1 and "DELETE /auth/generate-otp" in mismatch[0]


def test_param_segment_never_matches_a_literal_route_segment():
    assert cfc._matches("/a/{param}", "/a/{id}")
    assert cfc._matches("/a/add", "/a/{id}")
    assert not cfc._matches("/a/{param}", "/a/add")
    assert not cfc._matches("/a/{param}/b", "/a/{id}")


def test_extractor_reads_template_literals_and_generics(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "demo.ts").write_text(
        "export const a = () => api.get<unknown>(`/x/${seg(requireId(id, 'id'))}/y`, { q: 1 });\n"
        "export const b = () => api.post<unknown>(\n  '/z/add',\n  { n: 1 },\n);\n"
        "export const c = () => api.upload<unknown>(`/u/${seg(id)}`, file, { method: 'PUT' });\n"
        "export const d = () => api.put<unknown>(`/p/q${query}`);\n",
        encoding="utf-8",
    )
    got = {(c["method"], c["path"]) for c in cfc.extract_calls(tmp_path)}
    assert got == {("GET", "/x/{param}/y"), ("POST", "/z/add"), ("PUT", "/u/{param}"), ("PUT", "/p/q")}


def test_vendored_list_matches_the_live_frontend_when_present():
    if not (cfc.DEFAULT_FE / "src" / "api").is_dir():
        return
    live = {(c["method"], c["path"]) for c in cfc.extract_calls(cfc.DEFAULT_FE)}
    vendored = {(c["method"], c["path"]) for c in cfc.load_vendored()}
    if live != vendored:
        warnings.warn("tests/data/frontend_calls.json is stale: run `python scripts/check_frontend_contract.py --regenerate`. "
                      "added: %s removed: %s" % (sorted(live - vendored, key=str)[:5], sorted(vendored - live, key=str)[:5]))
