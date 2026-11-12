"""Frontend -> backend route coverage.

Extracts every METHOD + path the Clockyn app calls (`<FE>/src/api/**/*.ts`: `api.get/post/put/delete/patch/upload/request`
with string or template-literal paths, `${...}` -> `{param}`) and checks each against the backend routes that app.py
really mounts (AST of router/*.py, the same parser as tests/test_route_table.py; nothing is imported).

    python scripts/check_frontend_contract.py                     # check the vendored list tests/data/frontend_calls.json
    python scripts/check_frontend_contract.py --regenerate        # re-extract from the frontend repo, rewrite the list, then check
    python scripts/check_frontend_contract.py --frontend <path>   # frontend repo root (default C:/POC/clockyn_frontend or $CLOCKYN_FRONTEND)

Exit code 1 when a call has no route (or only a route with another method), except the KNOWN_ABSENT entries below.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

BE = Path(__file__).resolve().parents[1]
VENDORED = BE / "tests" / "data" / "frontend_calls.json"
DEFAULT_FE = Path(os.environ.get("CLOCKYN_FRONTEND", r"C:\POC\clockyn_frontend"))

# Calls the app makes that the backend deliberately does not (yet) serve. The key is (METHOD, normalised path with `{}`).
# Every entry needs a reason. A stale entry (the route now exists) is reported too, so the list can only shrink.
KNOWN_ABSENT: dict = {
    # (none at the time of writing)
}

# Calls whose path is built from a variable, so the regex cannot read it. (file, METHOD, path)
DYNAMIC_CALLS = (
    ("auth.ts", "POST", "/auth/login"),            # login(): portal === 'employee' ? '/auth/employee-login' : '/auth/login'
    ("auth.ts", "POST", "/auth/employee-login"),
)

_CALL = re.compile(r"\bapi\s*\.\s*(get|post|put|delete|patch|upload|request)\s*(?:<[^()]*?>)?\s*\(", re.S)


def _balanced(text: str, start: int):
    """Index after the matching ')' for the '(' at start-1 (strings and template literals are skipped)."""
    depth, i, n = 1, start, len(text)
    while i < n and depth:
        ch = text[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                elif quote == "`" and text[i] == "$" and text[i + 1:i + 2] == "{":
                    i = _skip_braces(text, i + 1)
                    continue
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return i


def _skip_braces(text: str, i: int) -> int:
    """i points at '{'; returns the index of its matching '}' (strings inside are skipped)."""
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _literal(text: str, i: int):
    """(path, end) for a string / template literal starting at text[i], else None. `${...}` becomes `{param}`."""
    if text[i] not in "'\"`":
        return None
    quote = text[i]
    out, j = [], i + 1
    while j < len(text) and text[j] != quote:
        if text[j] == "\\":
            out.append(text[j + 1])
            j += 2
        elif quote == "`" and text[j] == "$" and text[j + 1:j + 2] == "{":
            j = _skip_braces(text, j + 1) + 1
            out.append("{param}")
        else:
            out.append(text[j])
            j += 1
    return "".join(out), j + 1


def _clean(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"(?<=[^/])\{param\}$", "", path)          # `/x${buildQuery(..)}`: a glued suffix is a query string
    return re.sub(r"/+", "/", path).rstrip("/") or "/"


# Paths the app builds from a variable that holds a closed set of values (the regex sees `{param}` there).
# (file, path as extracted) -> the concrete paths. Keep in sync with src/api/payroll.ts / home.ts.
_KINDS = ("count", "turnover", "overtime", "bonus")
EXPANSIONS = {
    ("payroll.ts", "{param}/templates/{param}"): ["/api/daily-wages/templates/{param}", "/api/hourly-charges/templates/{param}"],
    ("payroll.ts", "{param}/template"): ["/api/daily-wages/template", "/api/hourly-charges/template"],
    ("payroll.ts", "{param}/template/{param}"): ["/api/daily-wages/template/{param}", "/api/hourly-charges/template/{param}"],
    ("payroll.ts", "/api/wages/{param}/{param}/{param}"): ["/api/wages/assigned/{param}/{param}", "/api/wages/unassigned/{param}/{param}"],
    ("payroll.ts", "/api/incentive/{param}/{param}"): ["/api/incentive/%s/{param}" % k for k in _KINDS],
    ("payroll.ts", "/api/incentive/{param}/details/{param}"): ["/api/incentive/%s/details/{param}" % k for k in _KINDS],
    ("payroll.ts", "/api/incentive/{param}/{param}/{param}"): ["/api/incentive/assigned/{param}/{param}", "/api/incentive/unassigned/{param}/{param}"],
    ("home.ts", None): ["/home/absent-staff", "/home/late-punched-in-staff", "/home/punched-in-staff", "/home/working-staff",
                        "/home/punched-out-staff"],
}


def extract_calls(fe_root: Path) -> list:
    """[{file, line, method, path}] sorted; plus `unresolved` entries are returned with path None."""
    api_dir = Path(fe_root) / "src" / "api"
    calls = []
    for file in sorted(api_dir.rglob("*.ts")):
        if "__tests__" in file.parts or "testUtils" in file.parts or file.name.endswith((".test.ts", ".spec.ts")):
            continue
        text = file.read_text(encoding="utf-8-sig")
        for m in _CALL.finditer(text):
            verb = m.group(1)
            end = _balanced(text, m.end())
            args = text[m.end():end - 1]
            k = 0
            while k < len(args) and args[k].isspace():
                k += 1
            if verb == "request":  # api.request('PUT', path, ...): the method is the first argument
                lit = _literal(args, k)
                if not lit:
                    calls.append(dict(file=file.name, line=text.count("\n", 0, m.start()) + 1, method="?", path=None))
                    continue
                method = lit[0].upper()
                k = lit[1]
                while k < len(args) and args[k] in " \t\r\n,":
                    k += 1
            elif verb == "upload":
                mm = re.search(r"method\s*:\s*['\"](POST|PUT|PATCH)['\"]", args)
                method = mm.group(1) if mm else "POST"
            else:
                method = verb.upper()
            lit = _literal(args, k)
            line = text.count("\n", 0, m.start()) + 1
            path = _clean(lit[0]) if lit else None
            for p in EXPANSIONS.get((file.name, path), [path]):
                calls.append(dict(file=file.name, line=line, method=method, path=p))
    return calls


def app_public_paths(fe_root: Path):
    """(PUBLIC_EXACT list, PUBLIC_PREFIXES list) of src/lib/api/client.ts: the paths the app sends WITHOUT a token."""
    text = (Path(fe_root) / "src" / "lib" / "api" / "client.ts").read_text(encoding="utf-8-sig")
    exact = re.search(r"PUBLIC_EXACT\s*=\s*new Set\(\[(.*?)\]\)", text, re.S).group(1)
    prefixes = re.search(r"PUBLIC_PREFIXES\s*=\s*\[(.*?)\]", text, re.S).group(1)
    return re.findall(r"'([^']+)'", exact), re.findall(r"'([^']+)'", prefixes)


# ---------------------------------------------------------------------------------------------------- backend
def _route_table():
    spec = importlib.util.spec_from_file_location("_route_table", BE / "tests" / "test_route_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def backend_routes() -> list:
    """[(file, METHOD, normalised path)] of every mounted route."""
    return _route_table().mounted_routes()


def _segs(path: str):
    return [s for s in path.split("/") if s]


def _matches(call_path: str, route_path: str) -> bool:
    """A literal call segment matches the same literal or a route parameter; a call parameter only a route parameter."""
    a, b = _segs(call_path), _segs(route_path)
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        cx, cy = x.startswith("{"), y.startswith("{")
        if cy:
            continue
        if cx or x != y:
            return False
    return True


def check(calls: list, routes=None):
    """Returns (no_route, method_mismatch, unresolved, stale_known_absent): lists of strings."""
    routes = routes if routes is not None else backend_routes()
    by_path = {}
    for _f, method, path in routes:
        by_path.setdefault(path, set()).add(method)
    no_route, mismatch, unresolved = [], [], []
    seen_absent = set()
    for c in calls:
        if c["path"] is None:
            if not any(d[0] == c["file"] for d in DYNAMIC_CALLS):
                unresolved.append("%s:%s api call with a non-literal path" % (c["file"], c["line"]))
            continue
        key = (c["method"], re.sub(r"\{[^}/]*\}", "{}", c["path"]))
        methods = set()
        for rpath, rm in by_path.items():
            if _matches(c["path"], rpath):
                methods |= rm
        if c["method"] in methods:
            continue
        if key in KNOWN_ABSENT:
            seen_absent.add(key)
            continue
        label = "%s %s  (%s:%s)" % (c["method"], c["path"], c["file"], c["line"])
        (mismatch if methods else no_route).append(label + (("  backend has: " + ",".join(sorted(methods))) if methods else ""))
    for f, method, path in DYNAMIC_CALLS:
        if not any(_matches(path, rp) and method in rm for rp, rm in by_path.items()):
            no_route.append("%s %s  (%s, dynamic)" % (method, path, f))
    stale = ["%s %s" % k for k in KNOWN_ABSENT if k not in seen_absent]
    return no_route, mismatch, unresolved, stale


def load_vendored(path: Path = VENDORED) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["calls"]


def write_vendored(calls: list, path: Path = VENDORED) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = [dict(file=c["file"], line=c["line"], method=c["method"], path=c["path"]) for c in calls]
    path.write_text(json.dumps({"note": "generated by scripts/check_frontend_contract.py --regenerate", "calls": slim}, indent=1) + "\n",
                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--frontend", default=str(DEFAULT_FE))
    args = ap.parse_args(argv)
    fe = Path(args.frontend)
    if args.regenerate:
        if not (fe / "src" / "api").is_dir():
            print("frontend not found at %s" % fe)
            return 2
        write_vendored(extract_calls(fe))
        print("wrote %s" % VENDORED)
    calls = load_vendored()
    no_route, mismatch, unresolved, stale = check(calls)
    print("%d calls checked" % len(calls))
    for title, rows in (("NO BACKEND ROUTE", no_route), ("METHOD MISMATCH", mismatch), ("UNRESOLVED", unresolved),
                        ("STALE KNOWN_ABSENT", stale)):
        if rows:
            print("\n" + title)
            print("\n".join("  " + r for r in rows))
    return 1 if (no_route or mismatch or unresolved or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
