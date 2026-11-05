"""DB-free tests for the connection helpers in core_functions/db_function.py, using a fake pool.
Run: python -m pytest tests/test_db_pool.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import db_function as dbf  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        if self.conn.fail_setup:
            raise RuntimeError("server closed the connection unexpectedly")
        self.conn.statements.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, fail_setup=False, closed=False):
        self.fail_setup = fail_setup
        self.closed = closed
        self.statements = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakePool:
    """Mimics ThreadedConnectionPool bookkeeping: a connection is 'used' until putconn() returns."""

    def __init__(self, conns, putconn_error_first=False):
        self.free = list(conns)
        self.used = []
        self.discarded = []
        self.put_calls = []
        self.putconn_error_first = putconn_error_first

    def getconn(self):
        if not self.free:
            raise RuntimeError("connection pool exhausted")
        conn = self.free.pop(0)
        self.used.append(conn)
        return conn

    def putconn(self, conn, key=None, close=False):
        self.put_calls.append((conn, close))
        if self.putconn_error_first:
            self.putconn_error_first = False
            raise RuntimeError("rollback of a broken connection failed")  # slot stays marked as used (psycopg2 behaviour)
        self.used.remove(conn)
        if close:
            conn.closed = True
            self.discarded.append(conn)
        else:
            self.free.append(conn)


@pytest.fixture(autouse=True)
def schema(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA", "punchapp_prod")


def install(monkeypatch, pool):
    monkeypatch.setattr(dbf, "_connection_pool", pool)
    return pool


def test_get_connection_sets_the_search_path_and_checks_out_one_connection(monkeypatch):
    c = FakeConn()
    pool = install(monkeypatch, FakePool([c]))
    assert dbf.get_db_connection() is c
    assert c.statements == ["SET search_path TO punchapp_prod"] and c.commits == 1
    assert pool.used == [c]
    dbf.return_connection(c)
    assert pool.used == [] and pool.free == [c]


def test_a_failed_setup_does_not_leak_the_pool_slot_and_a_fresh_connection_is_used(monkeypatch):
    bad, good = FakeConn(fail_setup=True), FakeConn()
    pool = install(monkeypatch, FakePool([bad, good]))
    assert dbf.get_db_connection() is good
    assert pool.discarded == [bad] and bad.closed
    assert pool.used == [good]


def test_two_failed_setups_raise_500_and_leave_nothing_checked_out(monkeypatch):
    pool = install(monkeypatch, FakePool([FakeConn(fail_setup=True), FakeConn(fail_setup=True)]))
    with pytest.raises(HTTPException) as e:
        dbf.get_db_connection()
    assert e.value.status_code == 500 and e.value.detail == "Database connection failed"
    assert pool.used == [] and len(pool.discarded) == 2


def test_repeated_failures_never_exhaust_the_pool(monkeypatch):
    pool = install(monkeypatch, FakePool([FakeConn(fail_setup=True) for _ in range(40)]))
    for _ in range(20):
        with pytest.raises(HTTPException):
            dbf.get_db_connection()
    assert pool.used == []


def test_a_closed_connection_from_the_pool_is_discarded(monkeypatch):
    stale, good = FakeConn(closed=True), FakeConn()
    pool = install(monkeypatch, FakePool([stale, good]))
    assert dbf.get_db_connection() is good
    assert pool.discarded == [stale] and pool.used == [good]


def test_pool_exhaustion_is_a_500_without_touching_other_connections(monkeypatch):
    pool = install(monkeypatch, FakePool([]))
    with pytest.raises(HTTPException) as e:
        dbf.get_db_connection()
    assert e.value.status_code == 500 and pool.used == []


def test_returning_a_closed_connection_frees_its_slot(monkeypatch):
    c = FakeConn()
    pool = install(monkeypatch, FakePool([c]))
    dbf.get_db_connection()
    c.closed = True  # the server dropped it while in use
    dbf.return_connection(c)
    assert pool.used == [] and pool.discarded == [c]


def test_a_putconn_failure_is_retried_as_a_discard(monkeypatch):
    c = FakeConn()
    pool = install(monkeypatch, FakePool([c], putconn_error_first=True))
    dbf.get_db_connection()
    dbf.return_connection(c)
    assert pool.used == [] and pool.discarded == [c]
    assert [close for _, close in pool.put_calls] == [False, True]


def test_return_connection_ignores_none_and_a_missing_pool(monkeypatch):
    pool = install(monkeypatch, FakePool([]))
    dbf.return_connection(None)
    assert pool.put_calls == []
    monkeypatch.setattr(dbf, "_connection_pool", None)
    dbf.return_connection(FakeConn())  # must not raise
    dbf.close_connection(FakeConn())
