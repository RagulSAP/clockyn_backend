"""List every requirement in requirements.txt and flag the ones that are not pinned to an exact version.

Stdlib only (runs anywhere, no install step):

    python scripts/dependency_report.py                  # report; exit code 1 when something is unpinned
    python scripts/dependency_report.py path/to/other.txt

A requirement counts as pinned when it is `name==version` (an extras suffix such as `uvicorn[standard]==0.24.0` is
fine). Ranges (`>=`, `~=`, `<`), bare names, URLs and `-r`/`-e` includes are reported as UNPINNED so that a floating
dependency cannot slip into the image build unnoticed (see README "Dependencies").
"""
import os
import re
import sys

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")

# name, optional [extras], '==', exact version (digits/letters/dots/+/!/- , no wildcard). Environment markers are
# allowed after a ';' (they do not change whether the version is pinned).
_PINNED = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[A-Za-z0-9._,\s-]+\])?"
    r"\s*==\s*(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*(;.*)?$"
)


def parse_requirements(text):
    """Return a list of dicts {line_no, raw, name, version, pinned} for every requirement line.

    Blank lines and full-line comments are skipped; a trailing ` # comment` is stripped.
    """
    rows = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        m = _PINNED.match(line)
        if m and "*" not in m.group("version"):
            rows.append({"line_no": line_no, "raw": line, "name": m.group("name"),
                         "version": m.group("version"), "pinned": True})
        else:
            name = re.split(r"[\s\[<>=!~;@]", line, maxsplit=1)[0] or line
            rows.append({"line_no": line_no, "raw": line, "name": name, "version": None, "pinned": False})
    return rows


def load(path=DEFAULT_FILE):
    with open(path, "r", encoding="utf-8") as fh:
        return parse_requirements(fh.read())


def format_report(rows):
    width = max([len(r["name"]) for r in rows] + [4])
    out = [f"{'NAME'.ljust(width)}  {'VERSION'.ljust(14)}  STATUS"]
    for r in rows:
        status = "pinned" if r["pinned"] else "UNPINNED  <-- line %d: %s" % (r["line_no"], r["raw"])
        out.append(f"{r['name'].ljust(width)}  {(r['version'] or '-').ljust(14)}  {status}")
    unpinned = [r for r in rows if not r["pinned"]]
    out.append("")
    out.append(f"{len(rows)} requirement(s), {len(rows) - len(unpinned)} pinned, {len(unpinned)} unpinned")
    return "\n".join(out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else DEFAULT_FILE
    rows = load(path)
    print(format_report(rows))
    return 1 if any(not r["pinned"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
