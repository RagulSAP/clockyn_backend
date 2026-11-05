"""A routed fake cursor / connection for the payroll tests (not a test module).

`RoutedCursor(routes)`: `routes` is a list of `(needle, result)`; the FIRST route whose needle occurs in the
whitespace-normalised SQL answers the statement. `result` is a list of rows (dicts or tuples), a callable
`params -> rows`, or an Exception instance (raised by `execute`). Statements without a route return no rows.
`SAVEPOINT` statements are accepted and recorded like any other.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RoutedCursor:
    def __init__(self, routes=None, rowcounts=None):
        self.routes = list(routes or [])
        self.rowcounts = dict(rowcounts or {})
        self.executed = []
        self._rows = []
        self.rowcount = 0
        self.closed = False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        self._rows = []
        self.rowcount = 0
        for needle, result in self.routes:
            if needle in norm:
                rows = result(params) if callable(result) else result
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows or [])
                self.rowcount = self.rowcounts.get(needle, len(self._rows))
                return
        for needle, count in self.rowcounts.items():
            if needle in norm:
                self.rowcount = count

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def close(self):
        self.closed = True

    # ---- inspection helpers
    def find(self, needle):
        return [(s, p) for s, p in self.executed if needle in s]

    def sql_text(self):
        return "\n".join(s for s, _ in self.executed)

    def non_savepoint(self):
        return [(s, p) for s, p in self.executed if not re.match(r"(SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT)", s)]


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def patch_db(monkeypatch, module, cursor):
    """Make `module`'s `get_db_connection` / `return_connection` use the fake cursor; returns the connection."""
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


def forbid_db(monkeypatch, module):
    """Any attempt to open a database connection fails the test (validation must happen before the database)."""
    def boom():
        raise AssertionError("the database must not be touched")
    monkeypatch.setattr(module, "get_db_connection", boom)
