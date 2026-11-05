"""Every environment variable the backend reads must be documented (README.md, Environment Variables table)
and present in .env (commented or placeholder). Scans the Python sources with `ast`; no import of the app.
Run: python -m pytest tests/test_env_documented.py -q
"""
import ast
import re

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["core_functions", "router", "services", "utils", "ml"]
SCAN_FILES = ["app.py", "migrate.py", "socket_manager.py"]

# OS / third-party level variables the code merely sets or tolerates; they are not configuration of the product.
ALLOWED = {
    "PATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "GLOG_minloglevel", "TF_CPP_MIN_LOG_LEVEL",
    "WATCHFILES_IGNORE_PATHS", "HOME", "TZ",
}
# Names that are intentionally documented under another spelling: the lowercase fallback of USER_AUTH.
ALIASES = {"user_auth": "USER_AUTH"}
# Reads whose name is built at run time: prefix -> README must show `<PREFIX><RULE>`-style row.
DYNAMIC_PREFIXES = {"RATE_LIMIT_"}


def _py_files():
    for d in SCAN_DIRS:
        yield from sorted((ROOT / d).glob("*.py"))
    for f in SCAN_FILES:
        p = ROOT / f
        if p.exists():
            yield p


def _is_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _const_str(node, consts):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    return None


def scan_env_names(files=None):
    """-> (names {name: {file,...}}, dynamic {file: [source of the non-literal name expression]})."""
    names: dict = {}
    dynamic: dict = {}
    parsed = []
    for p in (files if files is not None else _py_files()):
        tree = ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
        parsed.append((p, tree))

    def add(name, p):
        names.setdefault(name, set()).add(p.name)

    # helper readers: a function whose parameter is passed (directly) to os.getenv / os.environ.get or to another
    # helper reader (followed until nothing changes, e.g. branding._env -> branding._static_file)
    helpers: dict = {}   # function name -> positional index of the name parameter
    changed = True
    while changed:
        changed = False
        for p, tree in parsed:
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = [a.arg for a in fn.args.args]
                for call in ast.walk(fn):
                    if not isinstance(call, ast.Call):
                        continue
                    idx = 0 if _reads_env(call) else helpers.get(_callee_name(call))
                    if idx is None or len(call.args) <= idx:
                        continue
                    arg = call.args[idx]
                    if isinstance(arg, ast.Name) and arg.id in params and helpers.get(fn.name) != params.index(arg.id):
                        helpers[fn.name] = params.index(arg.id)
                        changed = True

    for p, tree in parsed:
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                consts[node.targets[0].id] = node.value.value
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if _reads_env(node) and node.args:
                    s = _const_str(node.args[0], consts)
                    if s is not None:
                        add(s, p)
                    elif not (isinstance(node.args[0], ast.Name) and _inside_helper(tree, node, helpers)):
                        dynamic.setdefault(p.name, []).append(ast.unparse(node.args[0]))
                fname = _callee_name(node)
                if fname in helpers and len(node.args) > helpers[fname]:
                    s = _const_str(node.args[helpers[fname]], consts)
                    if s is not None:
                        add(s, p)
                    elif not _inside_helper(tree, node, helpers):
                        dynamic.setdefault(p.name, []).append(ast.unparse(node.args[helpers[fname]]))
            elif isinstance(node, ast.Subscript) and _is_environ(node.value):
                s = _const_str(node.slice, consts)
                if s is not None:
                    add(s, p)
    return names, dynamic


def _callee_name(call: ast.Call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)


def _reads_env(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute):
        if f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os":
            return True
        if f.attr in ("get", "setdefault", "pop") and _is_environ(f.value):
            return True
    return False


def _inside_helper(tree, target, helpers) -> bool:
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in helpers:
            if any(n is target for n in ast.walk(fn)):
                return True
    return False


def readme_env_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^## Environment Variables\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, "README.md has no '## Environment Variables' section"
    return m.group(1)


def env_file_names() -> set:
    out = set()
    p = ROOT / ".env"
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            out.add(m.group(1))
    return out


def _documented_in(section: str, name: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", section) is not None


def _wanted():
    names, dynamic = scan_env_names()
    wanted = {}
    for n, files in names.items():
        if n in ALLOWED:
            continue
        wanted[ALIASES.get(n, n)] = files
    return wanted, dynamic


def test_scanner_finds_the_known_variables():
    names, _ = scan_env_names()
    for known in ("DB_HOST", "OTP_MAX_PER_HOUR", "FACE_MAX_WIDTH", "SELLER_NAME", "UPLOAD_MAX_MB", "AUTH_TOKEN_SECRET",
                  "REQUIRE_AUTH", "RUN_MIGRATIONS", "DEFAULT_ODOMETER_RATE", "OPERATOR_API_KEY"):
        assert known in names, f"scanner missed {known} (helper readers / constants not followed?)"
    assert len(names) > 60


def test_dynamic_env_names_are_only_the_known_prefixes():
    _, dynamic = scan_env_names()
    for fname, exprs in dynamic.items():
        for e in exprs:
            assert any(pref in e for pref in DYNAMIC_PREFIXES), (
                f"{fname}: environment name built at run time ({e}); add its documented prefix to DYNAMIC_PREFIXES")


def test_dynamic_prefix_rules_documented():
    section = readme_env_section()
    for pref in DYNAMIC_PREFIXES:
        assert pref in section, f"README environment table must document the {pref}<RULE> family"


def test_every_env_variable_is_documented_in_readme():
    wanted, _ = _wanted()
    section = readme_env_section()
    missing = sorted(n for n in wanted if not _documented_in(section, n))
    assert not missing, "not in the README Environment Variables table: " + ", ".join(
        f"{n} ({'/'.join(sorted(wanted[n]))})" for n in missing)


def test_every_env_variable_is_present_in_dot_env():
    wanted, _ = _wanted()
    present = env_file_names()
    missing = sorted(n for n in wanted if n not in present)
    assert not missing, "not in .env (add a commented placeholder): " + ", ".join(
        f"{n} ({'/'.join(sorted(wanted[n]))})" for n in missing)


def _env_is_git_ignored() -> bool:
    for gi in (ROOT / ".gitignore", ROOT.parent / ".gitignore"):
        try:
            lines = [ln.strip() for ln in gi.read_text(encoding="utf-8", errors="replace").splitlines()]
        except OSError:
            continue
        if ".env" in lines or "/.env" in lines:
            return True
    return False


def test_dot_env_has_no_real_looking_secret_values():
    """A `.env` that could be committed must stay placeholders only (empty or containing a <...> marker).

    A git-ignored `.env` is the owner's local configuration and legitimately holds real values: the test is skipped then.
    The check reports only the KEY name, and asserts on a boolean, so a failure can never print a secret (pytest's
    assertion rewriting would show the value of a compared variable)."""
    if _env_is_git_ignored():
        pytest.skip(".env is git-ignored: real local values are expected")
    p = ROOT / ".env"
    offenders = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]*(?:SECRET|PASSWORD|PRIVATE_KEY|AUTH_KEY|API_KEY|ACCESS_KEY)[A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            value = m.group(2).strip()
            if not (value == "" or "<" in value):
                offenders.append(m.group(1))
    no_real_values = not offenders
    assert no_real_values, "non-placeholder value(s) in .env for: " + ", ".join(sorted(offenders))
