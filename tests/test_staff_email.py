"""DB-free tests for the staff email rule of the e-mail sign-in mode (USER_AUTH=email).
Run: python -m pytest tests/test_staff_email.py -q
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import add_staff_function as asf  # noqa: E402


class FakeCursor:
    def __init__(self, found=None):
        self.found = found
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.found


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("USER_AUTH", raising=False)
    monkeypatch.delenv("user_auth", raising=False)


def staff(email):
    return SimpleNamespace(email=email)


def test_otp_mode_leaves_the_email_optional_and_untouched():
    cur = FakeCursor()
    data = staff("")
    asf._check_staff_email(cur, data)
    assert cur.sql == [] and data.email == ""


@pytest.mark.parametrize("bad", ["", "   ", "no-at-sign", "a@b", "a b@c.com"])
def test_email_mode_requires_a_valid_email(monkeypatch, bad):
    monkeypatch.setenv("USER_AUTH", "email")
    with pytest.raises(HTTPException) as e:
        asf._check_staff_email(FakeCursor(), staff(bad))
    assert e.value.status_code == 400 and "valid email" in e.value.detail


def test_email_mode_normalises_and_checks_other_live_accounts(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    cur = FakeCursor(found=None)
    data = staff("  Aarav.Sharma@Example.COM ")
    asf._check_staff_email(cur, data)
    assert data.email == "aarav.sharma@example.com"
    sql, params = cur.sql[0]
    assert "LOWER(email) = LOWER(%s)" in sql and "status IN (0, 1)" in sql and "user_id <>" not in sql
    assert params == ("aarav.sharma@example.com",)


def test_email_mode_refuses_an_email_used_by_another_account(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    with pytest.raises(HTTPException) as e:
        asf._check_staff_email(FakeCursor(found={"user_id": "u9"}), staff("taken@example.com"))
    assert e.value.status_code == 400 and "already used" in e.value.detail


def test_update_excludes_the_user_being_edited(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    cur = FakeCursor(found=None)
    asf._check_staff_email(cur, staff("me@example.com"), exclude_user_id="u1")
    sql, params = cur.sql[0]
    assert "user_id <> %s" in sql and params == ("me@example.com", "u1")
