"""Which API calls each role area of the Clockyn app makes (static import-graph analysis of the frontend repo).

Areas (see <FE>/src/config/routes.ts): `src/app/staff/**` is used by role 3 (staff) and role 2 (manager);
`src/app/admin/**` by role 1 (admin) and, through the Settings shortcuts (features/employee/utils/managerShortcuts.ts),
by role 2 for the screens of the permission granted. For every screen file under `src/app` the import graph is walked
(`@/..` and relative imports, non-test files) and the `@/api/*` functions used are mapped to METHOD + path.

    python scripts/derive_frontend_role_calls.py [--frontend <path>]   # rewrites tests/data/frontend_role_calls.json

The result is vendored so tests/test_frontend_role_access.py works without the frontend repo. It over-approximates (a shared
component pulls in everything it can reach), so a failing check is a lead to read, not always a bug.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

BE = Path(__file__).resolve().parents[1]
OUT = BE / "tests" / "data" / "frontend_role_calls.json"
DEFAULT_FE = Path(os.environ.get("CLOCKYN_FRONTEND", r"C:\POC\pa_frontend\Clockyn"))

_spec = importlib.util.spec_from_file_location("check_frontend_contract", BE / "scripts" / "check_frontend_contract.py")
cfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfc)

# manager shortcut permission -> admin route prefixes (and staff-area routes) that shortcut opens
MANAGER_SCREENS = {
    "Staff Management": ["admin/staff"],
    "Branch Management": ["admin/branches"],
    "Attendance Reports": ["staff/reports"],
    "Salary Management": ["admin/payroll/payslips", "admin/payroll/daily-wages", "admin/payroll/hourly", "admin/payroll/monthly-salary",
                          "admin/payroll/salary-sheet"],
    "Holiday Management": ["admin/holidays"],
    "Shift Management": ["admin/shifts"],
    "Leads Management": ["admin/crm"],
    "Incentive Management": ["admin/payroll/count-basis", "admin/payroll/turnover", "admin/payroll/overtime", "admin/payroll/bonus",
                             "admin/payroll/incentive"],
    "Company Settings": ["admin/company"],
    "Requests Management": ["admin/requests"],
}

_DEF = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?const\s+(\w+)\s*(?::[^=]*)?=", re.M)
_IMPORT = re.compile(r"""(?:import|export)\s+(type\s+)?([^;'"]*?)\s*from\s*['"]([^'"]+)['"]""", re.S)
_SIDE = re.compile(r"""import\s*['"]([^'"]+)['"]""")


def _calls_with_pos(text: str, fname: str):
    """[(pos, method, path)] of every api call in the file text (reuses the extractor of check_frontend_contract)."""
    out = []
    for m in cfc._CALL.finditer(text):
        verb = m.group(1)
        end = cfc._balanced(text, m.end())
        args = text[m.end():end - 1].lstrip()
        if verb == "request":
            lit = cfc._literal(args, 0)
            if not lit:
                continue
            method, args = lit[0].upper(), args[lit[1]:].lstrip(" \t\r\n,")
        elif verb == "upload":
            mm = re.search(r"method\s*:\s*['\"](POST|PUT|PATCH)['\"]", args)
            method = mm.group(1) if mm else "POST"
        else:
            method = verb.upper()
        lit = cfc._literal(args, 0)
        path = cfc._clean(lit[0]) if lit else None
        for p in cfc.EXPANSIONS.get((fname, path), [path]):
            if p is None and fname == "auth.ts":
                for q in ("/auth/login", "/auth/employee-login"):
                    out.append((m.start(), "POST", q))
            elif p is not None:
                out.append((m.start(), method, p))
    return out


def api_function_map(api_dir: Path) -> dict:
    """{module: {function name: set((method, path))}} (a function includes the local functions it references)."""
    result = {}
    for file in sorted(api_dir.glob("*.ts")):
        text = file.read_text(encoding="utf-8-sig")
        defs = [(m.start(), m.group(1) or m.group(2)) for m in _DEF.finditer(text)]
        direct = {name: set() for _p, name in defs}
        calls = _calls_with_pos(text, file.name)
        bounds = [p for p, _n in defs] + [len(text) + 1]
        for pos, method, path in calls:
            for i, (start, name) in enumerate(defs):
                if start <= pos < bounds[i + 1]:
                    direct[name].add((method, path))
        refs = {}
        for i, (start, name) in enumerate(defs):
            body = text[start:bounds[i + 1]]
            refs[name] = {other for other in direct if other != name and re.search(r"\b%s\b" % re.escape(other), body)}
        closure = {}
        for name in direct:
            seen, stack, acc = set(), [name], set()
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                acc |= direct[n]
                stack.extend(refs.get(n, ()))
            closure[name] = acc
        result[file.stem] = closure
    return result


class Graph:
    def __init__(self, fe: Path):
        self.src = fe / "src"
        self.funcs = api_function_map(self.src / "api")
        self.cache = {}

    def resolve(self, spec: str, base: Path):
        if spec.startswith("@/"):
            target = self.src / spec[2:]
        elif spec.startswith("."):
            target = (base.parent / spec).resolve()
        else:
            return None
        for cand in (target.with_suffix(".ts") if not target.suffix else target, target.with_suffix(".tsx"), target / "index.ts",
                     target / "index.tsx", Path(str(target) + ".ts"), Path(str(target) + ".tsx")):
            if cand.is_file():
                return cand
        return None

    def parse(self, file: Path):
        """(calls set, imported files list) for one source file."""
        if file in self.cache:
            return self.cache[file]
        text = file.read_text(encoding="utf-8-sig")
        calls, deps = set(), []
        for m in _IMPORT.finditer(text):
            type_only, names, spec = m.group(1), m.group(2).strip(), m.group(3)
            if type_only or names.startswith("type "):
                continue
            target = self.resolve(spec, file)
            if target is None:
                continue
            rel = target.relative_to(self.src).as_posix() if self.src in target.parents else ""
            body_wo_import = text.replace(m.group(0), "")
            if rel.startswith("api/"):
                self._api_usage(rel, names, body_wo_import, calls)
                continue
            deps.append(target)
        for m in _SIDE.finditer(text):
            t = self.resolve(m.group(1), file)
            if t:
                deps.append(t)
        self.cache[file] = (calls, deps)
        return self.cache[file]

    def _api_usage(self, rel: str, names: str, body: str, calls: set):
        module = Path(rel).stem
        if module == "index":                       # `import { authApi } from '@/api'`: authApi -> module auth
            exports = {}
            idx = (self.src / "api" / "index.ts").read_text(encoding="utf-8-sig")
            for m in re.finditer(r"export\s+\*\s+as\s+(\w+)\s+from\s+'\./(\w+)'", idx):
                exports[m.group(1)] = m.group(2)
            for local, mod in self._names(names, exports):
                for fn in set(re.findall(r"\b%s\.(\w+)" % re.escape(local), body)):
                    calls |= self.funcs.get(mod, {}).get(fn, set())
            return
        funcs = self.funcs.get(module, {})
        ns = re.match(r"\*\s+as\s+(\w+)", names)
        if ns:
            for fn in set(re.findall(r"\b%s\.(\w+)" % re.escape(ns.group(1)), body)):
                calls |= funcs.get(fn, set())
            return
        inner = re.search(r"\{(.*)\}", names, re.S)
        for part in (inner.group(1).split(",") if inner else []):
            part = part.strip()
            if not part or part.startswith("type "):
                continue
            orig, _, alias = part.partition(" as ")
            local = (alias or orig).strip()
            if orig.strip() in funcs and re.search(r"\b%s\b" % re.escape(local), body):
                calls |= funcs[orig.strip()]

    @staticmethod
    def _names(names: str, exports: dict):
        inner = re.search(r"\{(.*)\}", names, re.S)
        for part in (inner.group(1).split(",") if inner else []):
            orig, _, alias = part.strip().partition(" as ")
            if orig.strip() in exports:
                yield (alias or orig).strip(), exports[orig.strip()]

    def closure_calls(self, entry: Path) -> set:
        seen, stack, acc = set(), [entry], set()
        while stack:
            f = stack.pop()
            if f in seen or "__tests__" in f.parts:
                continue
            seen.add(f)
            calls, deps = self.parse(f)
            acc |= calls
            stack.extend(deps)
        return acc


def derive(fe: Path) -> dict:
    g = Graph(fe)
    app = g.src / "app"
    screens = {}
    for f in sorted(app.rglob("*.ts*")):
        if f.suffix in (".ts", ".tsx"):
            screens[f.relative_to(app).with_suffix("").as_posix()] = sorted(g.closure_calls(f))
    staff, admin = set(), set()
    for name, calls in screens.items():
        if name.startswith("staff/"):
            staff |= set(map(tuple, calls))
        if name.startswith("admin/"):
            admin |= set(map(tuple, calls))
    manager = {}
    for perm, prefixes in MANAGER_SCREENS.items():
        acc = set()
        for name, calls in screens.items():
            if any(name == p or name.startswith(p + "/") or name.startswith(p + "-") for p in prefixes):
                acc |= set(map(tuple, calls))
        manager[perm] = acc
    ser = lambda s: [dict(method=m, path=p) for m, p in sorted(s)]
    return {
        "note": "generated by scripts/derive_frontend_role_calls.py",
        "public_exact": sorted(cfc.app_public_paths(fe)[0]),
        "public_prefixes": cfc.app_public_paths(fe)[1],
        "staff_area": ser(staff),
        "admin_area": ser(admin),
        "manager_screens": {k: ser(v) for k, v in manager.items()},
        "screens": {k: ser(set(map(tuple, v))) for k, v in screens.items()},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frontend", default=str(DEFAULT_FE))
    args = ap.parse_args(argv)
    data = derive(Path(args.frontend))
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("wrote %s: staff %d calls, admin %d, manager permissions %d" % (
        OUT, len(data["staff_area"]), len(data["admin_area"]), len(data["manager_screens"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
