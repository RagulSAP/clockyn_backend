"""Route-table test: no two mounted handlers may register the same METHOD + full path.

Static: router/*.py (and utils/email.py) are parsed with `ast` - nothing is imported, so no heavy library is needed.
Only routers that app.py really mounts are checked (the unmounted root chat.py is ignored).
Run: python -m pytest tests/test_route_table.py -q
"""
import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Known legacy duplicates: the handler that is registered FIRST in app.py wins, the others are dead code.
# Key: (METHOD, normalised path) -> the files that register it, in app.py mount order. Stale entries fail the
# test, so the list can only shrink. Currently empty: router/holiday.py is the only owner of /api/holidays*.
ALLOWED_DUPLICATES: dict = {}


def _norm(path: str) -> str:
    """`{id}` and `{name}` are the same route shape for FastAPI: compare with the parameter names erased."""
    return re.sub(r"\{[^}/]*\}", "{}", path)


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _prefix_of(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "prefix":
            return _str(kw.value)
    return ""


def _is_api_router(call) -> bool:
    return isinstance(call, ast.Call) and getattr(call.func, "id", getattr(call.func, "attr", None)) == "APIRouter"


def _decorator_route(dec):
    """(method, path) for `@x.get("/p")` / `@x.websocket("/p")`, else None. Returns (var, method, path)."""
    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name)):
        return None
    method = dec.func.attr
    if method not in HTTP_METHODS and method != "websocket":
        return None
    path = _str(dec.args[0]) if dec.args else None
    if path is None:
        for kw in dec.keywords:
            if kw.arg == "path":
                path = _str(kw.value)
    if path is None:
        return None
    return dec.func.value.id, ("WEBSOCKET" if method == "websocket" else method.upper()), path


def parse_module(file: Path) -> dict:
    """{router_var: [(METHOD, full_path), ...]} for one module; also resolves router factories (see wages.py)."""
    tree = ast.parse(file.read_text(encoding="utf-8-sig"))
    prefixes = {}          # module-level var -> prefix
    factories = {}         # function name -> (router var inside, param name carrying the prefix)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and _is_api_router(node.value):
            prefixes[node.targets[0].id] = _prefix_of(node.value) or ""
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Assign) and len(inner.targets) == 1 and isinstance(inner.targets[0], ast.Name)
                        and _is_api_router(inner.value)):
                    for kw in inner.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Name):
                            factories[node.name] = (inner.targets[0].id, kw.value.id, node)
    routes = defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in factories:
            for dec in node.decorator_list:
                r = _decorator_route(dec)
                if r and r[0] in prefixes:
                    routes[r[0]].append((r[1], prefixes[r[0]] + r[2]))
    # `daily_router = _template_router("/api/daily-wages", "D")`: routes declared inside the factory, prefix = first argument
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) in factories):
            var, _param, fn = factories[node.value.func.id]
            prefix = _str(node.value.args[0]) if node.value.args else None
            assert prefix is not None, f"{file.name}: cannot resolve the prefix of {node.targets[0].id}"
            for inner in ast.walk(fn):
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in inner.decorator_list:
                        r = _decorator_route(dec)
                        if r and r[0] == var:
                            routes[node.targets[0].id].append((r[1], prefix + r[2]))
    return routes


def mounted_routes():
    """[(file name, METHOD, normalised path), ...] in app.py include order, for every router app.py mounts."""
    app_tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
    module_files = {}
    for node in ast.walk(app_tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("router", "utils"):
            for alias in node.names:
                module_files[alias.asname or alias.name] = ROOT / node.module / f"{alias.name}.py"
    mounts = []  # (module alias, router var or None for "all routers of the module")
    for node in ast.walk(app_tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "include_router" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id in module_files:
                mounts.append((node.lineno, arg.value.id, arg.attr))
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Attribute) and isinstance(node.iter.value, ast.Name):
            if node.iter.value.id in module_files and node.iter.attr == "routers":
                mounts.append((node.lineno, node.iter.value.id, None))
    mounts.sort()
    out = []
    for _line, alias, var in mounts:
        file = module_files[alias]
        parsed = parse_module(file)
        for v, routes in parsed.items():
            if var is None or v == var:
                out.extend((file.name, m, _norm(p)) for m, p in routes)
    return out


def duplicates():
    seen = defaultdict(list)
    for file, method, path in mounted_routes():
        seen[(method, path)].append(file)
    return {k: v for k, v in seen.items() if len(v) > 1}


def test_parser_finds_the_known_routes():
    routes = {(m, p) for _f, m, p in mounted_routes()}
    assert ("POST", "/auth/generate-otp") in routes
    assert ("GET", "/addstaff/{}") in routes
    assert ("GET", "/api/daily-wages/templates/{}") in routes      # router factory in wages.py
    assert ("DELETE", "/api/wages/unassign/{}/{}/{}") in routes     # module-level router in wages.py
    assert ("WEBSOCKET", "/ws/chat/{}") in routes
    assert len(routes) > 250


def test_only_mounted_routers_are_read():
    files = {f for f, _m, _p in mounted_routes()}
    assert {"auth.py", "chat.py", "wages.py", "email.py", "salary_payments.py"} <= files
    assert len(files) >= 35


def test_no_two_mounted_handlers_share_a_method_and_path():
    unexpected = {k: v for k, v in duplicates().items() if ALLOWED_DUPLICATES.get(k) != v}
    assert not unexpected, (
        "duplicate (method, path) registrations - FastAPI would silently serve only the first:\n"
        + "\n".join(f"  {m} {p}: {files}" for (m, p), files in sorted(unexpected.items()))
    )


def test_allowed_duplicates_are_still_real():
    found = duplicates()
    stale = [k for k, v in ALLOWED_DUPLICATES.items() if found.get(k) != v]
    assert not stale, f"remove from ALLOWED_DUPLICATES (no longer duplicated): {stale}"
