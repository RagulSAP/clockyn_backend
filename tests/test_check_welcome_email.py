"""scripts/check_welcome_email.py (the "why did no welcome e-mail arrive" tool). DB-free: a scripted fake plays the database, boto3 is a stub.
Run: python -m pytest tests/test_check_welcome_email.py -q
"""
import io
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_welcome_email as chk  # noqa: E402
from core_functions import welcome_email as we  # noqa: E402

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


def admin_row(**over):
    row = {"user_id": "admin-1", "name": "Asha Rao", "email": "asha@example.com", "org_id": "org-1", "role_id": 1,
           "welcome_email_sent_at": None, "welcome_email_attempts": 0, "tried_recently": False,
           "welcome_email_last_attempt_at": None, "email_ok": True}
    row.update(over)
    return row


class World:
    def __init__(self):
        self.columns = list(chk.COLUMNS)
        self.row = admin_row()
        self.executed, self.commits, self.sent, self.sandbox = [], 0, [], False


class Cur:
    def __init__(self, w):
        self.w, self.res = w, None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.w.executed.append((s, params))
        if s.startswith("SELECT current_schema"):
            self.res = {"s": "clockyn_dev"}
        elif "information_schema.columns" in s:
            self.res = [{"column_name": c} for c in self.w.columns]
        elif s.startswith("SELECT u.user_id, u.name"):
            self.res = self.w.row
        elif s.startswith("SELECT org_name FROM org_info"):
            self.res = {"org_name": "Sunrise Traders"}
        elif s.startswith("SELECT start_date, end_date FROM free_trial"):
            self.res = {"start_date": date(2026, 11, 16), "end_date": date(2026, 12, 16)}
        else:
            self.res = None

    def fetchone(self):
        return self.res

    def fetchall(self):
        return self.res


class Conn:
    def __init__(self, w):
        self.w = w

    def cursor(self, cursor_factory=None):
        return Cur(self.w)

    def commit(self):
        self.w.commits += 1

    def rollback(self):
        pass


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", "1")
    monkeypatch.setenv("EMAIL_USER", "no-reply@example.com")
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    monkeypatch.setattr(we, "get_db_connection", lambda: Conn(w))
    monkeypatch.setattr(we, "return_connection", lambda c: None)

    class SES:
        def get_send_quota(self):
            return {"Max24HourSend": 200.0 if w.sandbox else 50000.0, "SentLast24Hours": 3.0}

        def get_identity_verification_attributes(self, Identities):
            return {"VerificationAttributes": {"no-reply@example.com": {"VerificationStatus": "Success"}}}

        def send_raw_email(self, **kw):
            w.sent.append(kw)

    boto = types.ModuleType("boto3")
    boto.client = lambda *a, **k: SES()
    boto.Session = lambda: types.SimpleNamespace(get_credentials=lambda: object())
    monkeypatch.setitem(sys.modules, "boto3", boto)
    return w


def run(*argv):
    out = io.StringIO()
    code = chk.main(["org-1", *argv], out=out)
    return code, out.getvalue()


def test_everything_fine_passes_and_changes_nothing(world):
    code, text = run()
    assert code == 0, text
    assert "this admin is eligible" in text and "ALL CHECKS PASSED" in text
    assert "the e-mail renders" in text and world.sent == []
    assert not [s for s, _ in world.executed if s.startswith(("UPDATE", "INSERT", "DELETE"))] and world.commits == 0


def test_the_switch_being_off_is_reported(world, monkeypatch):
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", "0")
    code, text = run()
    assert code == 1 and "the feature is OFF" in text


def test_a_missing_sender_is_reported(world, monkeypatch):
    monkeypatch.delenv("EMAIL_USER")
    code, text = run()
    assert code == 1 and "EMAIL_USER is NOT set" in text


def test_missing_columns_are_named(world):
    world.columns = ["welcome_email_attempts"]
    code, text = run()
    assert code == 1 and "missing the column(s): welcome_email_sent_at, welcome_email_last_attempt_at" in text


@pytest.mark.parametrize("over,reason", [
    ({"welcome_email_sent_at": NOW}, "already sent"),
    ({"welcome_email_attempts": 3}, "attempts are used up"),
    ({"role_id": 2}, "not a company admin"),
    ({"email_ok": False}, "blank or not valid"),
    ({"tried_recently": True, "welcome_email_attempts": 1}, "less than an hour ago"),
])
def test_the_rule_that_stops_the_e_mail_is_named(world, over, reason):
    world.row = admin_row(**over)
    code, text = run()
    assert code == 1 and "will NOT be sent" in text and reason in text


def test_the_sandbox_is_called_out(world):
    world.sandbox = True
    code, text = run()
    assert code == 1 and "SES SANDBOX" in text


def test_the_address_is_masked(world):
    _, text = run()
    assert "asha@example.com" not in text and "a***@example.com" in text


def test_send_test_sends_through_ses_without_touching_the_database(world):
    code, text = run("--send-test-to", "me@example.org")
    assert code == 0, text
    assert [m["Destinations"] for m in world.sent] == [["me@example.org"]] and world.sent[0]["Source"] == "no-reply@example.com"
    assert world.commits == 0


def test_a_refused_send_test_shows_the_aws_error(world, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("AccessDenied: not authorized to perform ses:SendRawEmail")
    monkeypatch.setattr(we, "send_email", refuse)
    code, text = run("--send-test-to", "me@example.org")
    assert code == 1 and "SES refused the test e-mail" in text and "ses:SendRawEmail" in text


def test_reset_clears_only_that_admin(world):
    code, text = run("--reset")
    updates = [(s, p) for s, p in world.executed if s.startswith("UPDATE users SET welcome_email_sent_at = NULL")]
    assert len(updates) == 1 and updates[0][1] == ("admin-1",) and world.commits == 1
    assert "cleared the welcome_email_* columns of admin admin-1" in text
