"""Fake database pieces shared by the WP7 tests (requests, shift resolution, device flow). No database needed.

`RouteCursor` answers statements by substring: the first route whose needle is in the (whitespace-normalised)
statement decides what `fetchone()` / `fetchall()` / `rowcount` return for it. Every statement is recorded.
"""
import re


def norm(sql) -> str:
    return " ".join(str(sql).split())


class RouteCursor:
    def __init__(self, routes=None, rowcount=1):
        # routes: list of (needle, spec); spec = dict(one=..., many=..., rowcount=...) values may be callables(params)
        self.routes = list(routes or [])
        self.default_rowcount = rowcount
        self.sql = []
        self.rowcount = rowcount
        self._one = None
        self._many = []

    def add(self, needle, one=None, many=None, rowcount=None):
        self.routes.append((needle, dict(one=one, many=many, rowcount=rowcount)))
        return self

    def execute(self, sql, params=None):
        s = norm(sql)
        self.sql.append((s, params))
        self._one, self._many, self.rowcount = None, [], self.default_rowcount
        for needle, spec in self.routes:
            if needle in s:
                one, many, rc = spec.get("one"), spec.get("many"), spec.get("rowcount")
                if callable(one):
                    one = one(params)
                if callable(many):
                    many = many(params)
                if isinstance(one, _Seq):
                    one = one.next()
                self._one = one
                self._many = list(many) if many is not None else []
                if rc is not None:
                    self.rowcount = rc
                break

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    # -- assertions helpers
    def find(self, text):
        return [(s, p) for s, p in self.sql if text in s]

    def has(self, text) -> bool:
        return bool(self.find(text))


class _Seq:
    """Different answers for successive executions of the same statement."""

    def __init__(self, values):
        self.values = list(values)

    def next(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def seq(*values):
    return _Seq(values)


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def patch_db(monkeypatch, module, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


def placeholders_match(sql: str, params) -> bool:
    """The number of %s placeholders equals the number of params (the classic untested-SQL mistake)."""
    count = len(re.findall(r"%s", sql))
    return count == (len(params) if params is not None else 0)
