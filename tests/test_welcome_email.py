"""The one-time welcome e-mail to a company admin (migration 042, core_functions/welcome_email.py, the Home hook).
DB-free: a small stateful fake plays the users / org_info / free_trial statements, SES (boto3) and the template are stubs.
Run: python -m pytest tests/test_welcome_email.py -q
"""
import asyncio
import email
import email.policy
import re
import sys
import threading
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import home_functions as home  # noqa: E402
from core_functions import pricing  # noqa: E402
from core_functions import welcome_email as we  # noqa: E402

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


def norm(sql):
    return " ".join(sql.split())


# ═════════════════════════════════════════════════════════════════════════ fakes
class State:
    def __init__(self):
        self.now = NOW
        self.lock = threading.Lock()
        self.log = []
        self.users = {
            "admin-1": {"user_id": "admin-1", "name": "Asha Rao", "email": "asha@example.com", "org_id": "org-1", "role_id": 1,
                        "welcome_email_sent_at": None, "welcome_email_attempts": 0, "welcome_email_last_attempt_at": None},
            "mgr-1": {"user_id": "mgr-1", "name": "Ravi", "email": "ravi@example.com", "org_id": "org-1", "role_id": 2,
                      "welcome_email_sent_at": None, "welcome_email_attempts": 0, "welcome_email_last_attempt_at": None},
        }
        self.orgs = {"org-1": {"org_name": "Sunrise Traders", "parent_id": "admin-1"}}   # org_info.parent_id = the admin's user id
        self.trials = {"org-1": {"start_date": date(2026, 11, 16), "end_date": date(2026, 12, 16)}}


class Cur:
    def __init__(self, st):
        self.st, self.row = st, None

    def execute(self, sql, params=None):
        st, s = self.st, norm(sql)
        st.log.append((s, params))
        self.row = None
        if s.startswith("UPDATE users u SET welcome_email_attempts"):
            with st.lock:                                              # the real statement is atomic: row lock + re-check
                owner = (st.orgs.get(params[1]) or {}).get("parent_id")       # the id may be the organisation's (what the app sends)
                u = st.users.get(params[0]) or st.users.get(owner)
                if u and we.should_welcome(u, now=st.now):
                    u["welcome_email_attempts"] += 1
                    u["welcome_email_last_attempt_at"] = st.now
                    self.row = {k: u[k] for k in ("user_id", "name", "email", "org_id")}
        elif s.startswith("SELECT org_name FROM org_info"):
            self.row = st.orgs.get(params[0])
        elif s.startswith("SELECT start_date, end_date FROM free_trial"):
            self.row = st.trials.get(params[0])
        elif s.startswith("UPDATE users SET welcome_email_sent_at = NOW()"):
            u = st.users[params[0]]
            if u["welcome_email_sent_at"] is None:
                u["welcome_email_sent_at"] = st.now

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, st):
        self.st, self.commits, self.rollbacks = st, 0, 0

    def cursor(self, cursor_factory=None):
        return Cur(self.st)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Stub:
    """Plays utils.welcome_email_template and SES."""

    def __init__(self):
        self.rendered, self.sent, self.fail_send, self.fail_render, self.subject = [], [], False, False, "Template subject"
        self.logo_cid, self.logo_bytes, self.logos = None, None, []

    def render_welcome_email(self, **kw):
        if self.fail_render:
            raise ValueError("template exploded")
        self.rendered.append(kw)
        mail = types.SimpleNamespace(subject=self.subject, html="<p>Hello</p>", text="Hello")
        if self.logo_bytes is not None:
            mail.logo_cid, mail.logo_bytes = self.logo_cid, self.logo_bytes
        return mail

    def send_email(self, to, subject, html, text, logo_cid=None, logo_bytes=None):
        if self.fail_send:
            raise RuntimeError("SES said no")
        self.sent.append((to, subject, html, text))
        self.logos.append((logo_cid, logo_bytes))


@pytest.fixture
def env(monkeypatch):
    st, stub = State(), Stub()
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", "1")
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.setattr(we, "get_db_connection", lambda: Conn(st))
    monkeypatch.setattr(we, "return_connection", lambda c: None)
    monkeypatch.setattr(we, "send_email", stub.send_email)
    import utils
    monkeypatch.setitem(sys.modules, "utils.welcome_email_template", stub)
    monkeypatch.setattr(utils, "welcome_email_template", stub, raising=False)
    we._recent.clear()
    return types.SimpleNamespace(st=st, stub=stub)


# ═════════════════════════════════════════════════════════════════════════ the switch
@pytest.mark.parametrize("value,on", [("1", True), ("true", True), ("YES", True), (" on ", True), ("0", False), ("", False), ("off", False), ("no", False)])
def test_the_switch(monkeypatch, value, on):
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", value)
    assert we.enabled() is on


def test_unset_means_off(monkeypatch):
    monkeypatch.delenv("WELCOME_EMAIL_ENABLED", raising=False)
    assert we.enabled() is False


def test_off_means_no_query_no_thread_no_cache_entry(monkeypatch):
    monkeypatch.delenv("WELCOME_EMAIL_ENABLED", raising=False)
    we._recent.clear()
    monkeypatch.setattr(we, "get_db_connection", lambda: (_ for _ in ()).throw(AssertionError("no database access while off")))
    monkeypatch.setattr(we, "_run_safely", lambda uid: (_ for _ in ()).throw(AssertionError("no worker while off")))
    asyncio.run(we.schedule_welcome_email("admin-1"))
    assert we._recent == {}


# ═════════════════════════════════════════════════════════════════════════ the claim
def test_claim_sql_has_every_guard():
    sql = norm(we.CLAIM_SQL)
    assert sql.startswith("UPDATE users u SET welcome_email_attempts = u.welcome_email_attempts + 1, welcome_email_last_attempt_at = NOW()")
    assert "u.user_id = %s" in sql and "u.role_id = 1" in sql                                          # admins only
    assert "u.welcome_email_sent_at IS NULL" in sql                                                    # once
    assert "u.welcome_email_attempts < %s" in sql and we.MAX_ATTEMPTS == 3                             # at most 3 tries
    assert "u.welcome_email_last_attempt_at < NOW() - INTERVAL '1 hour'" in sql                        # 1 hour apart
    assert "u.email ~ " in sql                                                                         # a plausible address, else no attempt burnt
    assert sql.endswith("RETURNING u.user_id, u.name, u.email, u.org_id")


def test_the_sql_e_mail_pattern_accepts_and_refuses_the_same_addresses_as_python():
    m = re.search(r"u\.email ~ '([^']+)'", we.CLAIM_SQL).group(1)
    posix = m.replace("[:space:]", r"\s")
    for ok in ("asha@example.com", "a.b+c@sub.example.co.in"):
        assert re.match(posix, ok) and we.should_welcome({"role_id": 1, "email": ok})
    for bad in ("", "asha", "asha@", "@example.com", "asha@example", "a b@example.com", "asha@exa mple.com", "a@@b.com"):
        assert not re.match(posix, bad) and not we.should_welcome({"role_id": 1, "email": bad}), bad


@pytest.mark.parametrize("row,ok", [
    ({"role_id": 1, "email": "a@b.co"}, True),
    ({"role_id": 2, "email": "a@b.co"}, False),
    ({"role_id": 3, "email": "a@b.co"}, False),
    ({"role_id": 1, "email": "a@b.co", "welcome_email_sent_at": NOW}, False),
    ({"role_id": 1, "email": "a@b.co", "welcome_email_attempts": 3}, False),
    ({"role_id": 1, "email": "a@b.co", "welcome_email_attempts": 2, "welcome_email_last_attempt_at": NOW - timedelta(minutes=59)}, False),
    ({"role_id": 1, "email": "a@b.co", "welcome_email_attempts": 2, "welcome_email_last_attempt_at": NOW - timedelta(minutes=61)}, True),
    (None, False),
])
def test_should_welcome(row, ok):
    assert we.should_welcome(row, now=NOW) is ok


# ═════════════════════════════════════════════════════════════════════════ the flow
def test_first_home_load_for_an_admin_sends_once(env):
    assert we.process("admin-1") == "sent"
    to, subject, html, text = env.stub.sent[0]
    assert (to, subject, html, text) == ("asha@example.com", "Template subject", "<p>Hello</p>", "Hello")
    u = env.st.users["admin-1"]
    assert u["welcome_email_sent_at"] == NOW and u["welcome_email_attempts"] == 1
    assert env.stub.rendered == [{"first_name": "Asha", "org_name": "Sunrise Traders", "trial_end": date(2026, 12, 16),
                                  "staff_limit": pricing.TRIAL_STAFF_LIMIT, "trial_months": 1}]


def test_a_second_load_and_a_parallel_load_send_nothing_more(env):
    assert we.process("admin-1") == "sent"
    assert we.process("admin-1") == "skipped" and len(env.stub.sent) == 1


def test_two_parallel_loads_only_one_wins(env):
    results = []
    threads = [threading.Thread(target=lambda: results.append(we.process("admin-1"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results).count("sent") == 1 and results.count("skipped") == 7 and len(env.stub.sent) == 1


def test_managers_and_staff_never_claim(env):
    assert we.process("mgr-1") == "skipped" and we.process("nobody") == "skipped"
    assert env.stub.sent == [] and env.st.users["mgr-1"]["welcome_email_attempts"] == 0


def test_a_blank_or_invalid_address_is_skipped_without_burning_an_attempt(env):
    for bad in ("", "not-an-address", "a b@c.com"):
        env.st.users["admin-1"]["email"] = bad
        assert we.process("admin-1") == "skipped"
    u = env.st.users["admin-1"]
    assert u["welcome_email_attempts"] == 0 and u["welcome_email_sent_at"] is None and env.stub.sent == []
    env.st.users["admin-1"]["email"] = "asha@example.com"                      # the address is fixed later: still welcomed
    assert we.process("admin-1") == "sent"


def test_a_ses_failure_counts_the_attempt_and_retries_only_after_an_hour_at_most_three_times(env):
    env.stub.fail_send = True
    assert we.process("admin-1") == "failed"
    u = env.st.users["admin-1"]
    assert u["welcome_email_sent_at"] is None and u["welcome_email_attempts"] == 1
    assert we.process("admin-1") == "skipped" and u["welcome_email_attempts"] == 1          # too soon
    env.st.now += timedelta(minutes=61)
    assert we.process("admin-1") == "failed" and u["welcome_email_attempts"] == 2
    env.st.now += timedelta(minutes=61)
    assert we.process("admin-1") == "failed" and u["welcome_email_attempts"] == 3
    env.st.now += timedelta(days=2)
    assert we.process("admin-1") == "skipped" and u["welcome_email_attempts"] == 3          # gave up
    env.stub.fail_send = False
    assert we.process("admin-1") == "skipped" and env.stub.sent == []


def test_a_retry_that_works_sets_sent_at(env):
    env.stub.fail_send = True
    we.process("admin-1")
    env.stub.fail_send = False
    env.st.now += timedelta(hours=2)
    assert we.process("admin-1") == "sent"
    assert env.st.users["admin-1"]["welcome_email_sent_at"] == env.st.now and len(env.stub.sent) == 1


def test_a_template_error_is_a_counted_failure_not_an_exception(env):
    env.stub.fail_render = True
    assert we.process("admin-1") == "failed"
    assert env.st.users["admin-1"]["welcome_email_attempts"] == 1 and env.stub.sent == []


def test_a_database_error_never_raises(monkeypatch, env):
    monkeypatch.setattr(we, "get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert we.process("admin-1") == "failed"


def test_the_subject_comes_from_the_template_with_the_approved_fallback(env):
    env.stub.subject = ""
    we.process("admin-1")
    assert env.stub.sent[0][1] == "Welcome to Clockyn, Asha! Your 1-month free trial has started"


def test_subject_for(monkeypatch):
    assert we.subject_for("Asha Rao", 1) == "Welcome to Clockyn, Asha! Your 1-month free trial has started"
    assert we.subject_for("", 2) == "Welcome to Clockyn, there! Your 2-month free trial has started"
    monkeypatch.setenv("APP_NAME", "Acme Time")
    assert we.subject_for("Asha", 1).startswith("Welcome to Acme Time, Asha!")


def test_no_trial_row_is_handled(env):
    del env.st.trials["org-1"]
    assert we.process("admin-1") == "sent"
    assert env.stub.rendered[0]["trial_end"] is None and env.stub.rendered[0]["trial_months"] == we.TRIAL_MONTHS


def test_first_name_and_trial_months():
    assert we.first_name("  Asha   Rao ") == "Asha" and we.first_name("") == "there" and we.first_name(None) == "there"
    assert we.trial_months(date(2026, 11, 16), date(2026, 12, 16)) == 1
    assert we.trial_months(date(2026, 1, 31), date(2026, 2, 28)) == 1
    assert we.trial_months(date(2026, 11, 1), date(2027, 1, 1)) == 2
    assert we.trial_months(None, None) == we.TRIAL_MONTHS and we.trial_months(date(2026, 11, 16), date(2026, 11, 20)) == we.TRIAL_MONTHS


def test_the_body_is_never_logged(env, caplog):
    caplog.set_level("DEBUG")
    we.process("admin-1")
    assert "<p>Hello</p>" not in caplog.text and "asha@example.com" not in caplog.text


# ═════════════════════════════════════════════════════════════════════════ SES
class FakeSes:
    def __init__(self):
        self.calls, self.regions = [], []

    def client(self, name, region_name=None):
        assert name == "ses"
        self.regions.append(region_name)
        return types.SimpleNamespace(send_raw_email=lambda **kw: self.calls.append(kw),
                                     send_email=lambda **kw: (_ for _ in ()).throw(AssertionError("one path: send_raw_email")))


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=fake.client))
    monkeypatch.setenv("EMAIL_USER", "noreply@clockyn.test")
    monkeypatch.delenv("AWS_SES_REGION", raising=False)
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    return fake


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def parsed(call):
    return email.message_from_bytes(call["RawMessage"]["Data"], policy=email.policy.default)


def test_ses_raw_message_headers_and_envelope(ses):
    we.send_email("asha@example.com", "Hi there", "<b>x</b>", "x")
    call = ses.calls[0]
    assert call["Source"] == "noreply@clockyn.test" and call["Destinations"] == ["asha@example.com"] and ses.regions == ["ap-south-1"]
    msg = parsed(call)
    assert msg["From"] == "Clockyn <noreply@clockyn.test>" and msg["To"] == "asha@example.com" and str(msg["Subject"]) == "Hi there"
    assert msg["Reply-To"] is None


def test_without_a_logo_it_is_multipart_alternative_text_then_html_and_no_attachment(ses):
    we.send_email("asha@example.com", "Hi", "<b>x</b>", "plain text")
    msg = parsed(ses.calls[0])
    assert msg.get_content_type() == "multipart/alternative"
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_content().strip() == "plain text" and parts[1].get_content().strip() == "<b>x</b>"
    assert all(p.get_content_charset() == "utf-8" for p in parts)
    assert list(msg.iter_attachments()) == [] and not any(p.get_content_maintype() == "image" for p in msg.walk())


def test_with_a_logo_it_is_related_alternative_plus_an_inline_png(ses):
    we.send_email("asha@example.com", "Hi", "<img src=\"cid:clockyn-logo\">", "plain", logo_cid="clockyn-logo", logo_bytes=PNG)
    msg = parsed(ses.calls[0])
    assert msg.get_content_type() == "multipart/related"
    top = list(msg.iter_parts())
    assert [p.get_content_type() for p in top] == ["multipart/alternative", "image/png"]
    assert [p.get_content_type() for p in top[0].iter_parts()] == ["text/plain", "text/html"]
    image = top[1]
    assert image["Content-ID"] == "<clockyn-logo>"
    assert image.get_content_disposition() == "inline" and image.get_filename() == "clockyn-logo.png"
    assert image["Content-Transfer-Encoding"] == "base64" and image.get_content() == PNG


def test_the_content_id_must_be_a_safe_token(ses):
    for bad in (None, "", "a b", "x>\r\nBcc: evil@example.com", "<clockyn-logo>", "a" * 101):
        with pytest.raises(ValueError):
            we.send_email("asha@example.com", "Hi", "h", "t", logo_cid=bad, logo_bytes=PNG)
    assert ses.calls == []


def test_a_non_ascii_name_in_the_subject_is_rfc2047_encoded_and_decodes_back(ses):
    subject = "Welcome to Clockyn, Ananya \u0b85\u0ba9\u0ba9\u0bcd\u0ba9\u0bbe! Your 1-month free trial has started"
    we.send_email("asha@example.com", subject, "h", "t")
    raw = ses.calls[0]["RawMessage"]["Data"]
    header_line = next(line for line in raw.split(b"\r\n") + raw.split(b"\n") if line.startswith(b"Subject:"))
    assert b"=?utf-8?" in header_line.lower() and "\u0b85".encode() not in raw.split(b"\n\n")[0]
    assert str(parsed(ses.calls[0])["Subject"]) == subject


def test_a_newline_in_the_subject_cannot_add_a_header(ses):
    we.send_email("asha@example.com", "Welcome, Asha\r\nBcc: evil@example.com\nX-Evil: 1", "h", "t")
    raw = ses.calls[0]["RawMessage"]["Data"]
    head = raw.split(b"\n\n")[0].split(b"\r\n\r\n")[0]
    assert not re.search(rb"(?im)^(bcc|x-evil):", head)
    msg = parsed(ses.calls[0])
    assert msg["Bcc"] is None and msg["X-Evil"] is None and "Bcc: evil@example.com" in str(msg["Subject"])


def test_a_newline_in_the_recipient_is_refused(ses):
    with pytest.raises(ValueError):
        we.send_email("asha@example.com\r\nBcc: evil@example.com", "Hi", "h", "t")
    assert ses.calls == []


def test_a_newline_in_the_brand_name_or_support_address_cannot_add_a_header(ses, monkeypatch):
    monkeypatch.setenv("APP_NAME", "Clockyn\r\nBcc: evil@example.com")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@clockyn.test\r\nBcc: evil2@example.com")
    we.send_email("asha@example.com", "Hi", "h", "t")
    msg = parsed(ses.calls[0])
    assert msg["Bcc"] is None and len(msg.get_all("From")) == 1 and len(msg.get_all("Reply-To")) == 1


def test_reply_to_only_when_support_email_is_set(ses, monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", "help@clockyn.test")
    we.send_email("asha@example.com", "Hi", "h", "t")
    assert parsed(ses.calls[0])["Reply-To"] == "help@clockyn.test"


def test_region_and_brand_name_come_from_settings(ses, monkeypatch):
    monkeypatch.setenv("AWS_SES_REGION", "eu-west-1")
    monkeypatch.setenv("APP_NAME", "Acme, Time")
    we.send_email("asha@example.com", "Hi", "h", "t")
    assert ses.regions == ["eu-west-1"] and parsed(ses.calls[0])["From"].addresses[0].display_name == "Acme, Time"


def test_a_missing_sender_is_an_error(ses, monkeypatch):
    monkeypatch.delenv("EMAIL_USER", raising=False)
    with pytest.raises(RuntimeError):
        we.send_email("asha@example.com", "Hi", "h", "t")
    assert ses.calls == []


def test_process_passes_the_template_logo_to_the_sender(env):
    env.stub.logo_cid, env.stub.logo_bytes = "clockyn-logo", PNG
    assert we.process("admin-1") == "sent"
    assert env.stub.logos == [("clockyn-logo", PNG)]


def test_process_without_a_logo_passes_none(env):
    assert we.process("admin-1") == "sent"
    assert env.stub.logos == [(None, None)]


# ═════════════════════════════════════════════════════════════════════════ scheduling and the Home hook
def test_schedule_returns_at_once_and_runs_the_work_in_a_thread(monkeypatch, env):
    ran = threading.Event()
    seen = {}

    def fake(uid):
        seen["thread"], seen["uid"] = threading.current_thread(), uid
        ran.set()

    monkeypatch.setattr(we, "_run_safely", fake)

    async def go():
        await we.schedule_welcome_email("admin-1")
        assert not ran.is_set() or seen["thread"] is not threading.main_thread()

    asyncio.run(go())
    assert ran.wait(2) and seen["uid"] == "admin-1" and seen["thread"] is not threading.main_thread()


def test_the_same_admin_is_looked_at_once_per_ten_minutes(monkeypatch, env):
    calls = []
    monkeypatch.setattr(we, "_run_safely", lambda uid: calls.append(uid))

    async def go():
        for _ in range(5):
            await we.schedule_welcome_email("admin-1")
        await we.schedule_welcome_email("admin-2")
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert sorted(calls) == ["admin-1", "admin-2"]


def test_schedule_never_raises(monkeypatch, env):
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))
    asyncio.run(we.schedule_welcome_email("admin-1"))
    asyncio.run(we.schedule_welcome_email(None))
    asyncio.run(we.schedule_welcome_email(""))


def test_the_worker_swallows_everything(monkeypatch):
    monkeypatch.setattr(we, "process", lambda uid: (_ for _ in ()).throw(RuntimeError("boom")))
    we._run_safely("admin-1")


class _Stop(Exception):
    pass


def _drive_home(monkeypatch, scope, hook=None):
    """Run get_dashboard_status up to its first database access; returns what the hook was called with."""
    import core_functions.manager_functions as mf
    called = []

    async def fake_scope(user_id):
        return scope

    async def fake_schedule(user_id):
        called.append(user_id)
        if hook:
            raise hook

    monkeypatch.setattr(mf, "get_staff_query_scope", fake_scope)
    monkeypatch.setattr(we, "schedule_welcome_email", fake_schedule)
    monkeypatch.setattr(home, "get_db_connection", lambda: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):                                    # reaching the database means the hook did not break the request
        asyncio.run(home.get_dashboard_status("u1"))
    return called


def test_home_hands_an_admin_to_the_welcome_email(monkeypatch):
    assert _drive_home(monkeypatch, ("u1", None, None)) == ["u1"]


def test_home_does_not_hand_over_a_manager(monkeypatch):
    assert _drive_home(monkeypatch, ("org-1", [1], None)) == []
    assert _drive_home(monkeypatch, ("org-1", None, None)) == []          # a manager without assignments: scope org is the organisation


def test_a_failing_scheduler_never_reaches_the_home_response(monkeypatch):
    assert _drive_home(monkeypatch, ("u1", None, None), hook=RuntimeError("scheduler exploded")) == ["u1"]


# ═════════════════════════════════════════════════════════════════════════ the schema files
MIG = (ROOT / "migrations" / "042_welcome_email.sql").read_text(encoding="utf-8")
DDL = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
FRESH = (ROOT / "clockyn_ddl_fresh.sql").read_text(encoding="utf-8")


def code(sql):
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


def test_migration_has_one_guarded_block_and_no_unguarded_update():
    body = code(MIG)
    assert len(re.findall(r"\bDO\s+\$\$", body)) == 1
    block = re.search(r"DO\s+\$\$(.*?)\$\$;", body, re.S).group(1)
    assert "NOT EXISTS" in block and "information_schema.columns" in block and "welcome_email_sent_at" in block   # the column-does-not-exist guard
    guard_end = block.index("THEN")
    assert "ADD COLUMN" not in block[:guard_end] and "UPDATE" not in block[:guard_end]
    assert "UPDATE users SET welcome_email_sent_at = now() WHERE welcome_email_sent_at IS NULL" in block      # the one-time backfill, inside
    outside = (body[:body.index("DO $$")] + body[body.rindex("$$;") + 3:]).upper()
    assert "UPDATE" not in outside and "DELETE" not in outside and "DROP" not in outside


def test_migration_columns():
    assert "ADD COLUMN IF NOT EXISTS welcome_email_sent_at timestamptz NULL" in MIG
    assert "ADD COLUMN IF NOT EXISTS welcome_email_attempts int4 NOT NULL DEFAULT 0" in MIG
    assert "ADD COLUMN IF NOT EXISTS welcome_email_last_attempt_at timestamptz NULL" in MIG


def test_latest_ddl_has_the_columns_in_users_and_a_guarded_end_block():
    users = re.search(r"CREATE TABLE clockyn_dev\.users \((.*?)\n\);", DDL, re.S).group(1)
    assert "welcome_email_sent_at timestamptz NULL" in users
    assert "welcome_email_attempts int4 DEFAULT 0 NOT NULL" in users
    assert "welcome_email_last_attempt_at timestamptz NULL" in users
    tail = DDL[DDL.index("ALTER: one-time welcome e-mail columns"):]
    body = code(tail)
    assert len(re.findall(r"\bDO\s+\$\$", body)) == 1 and "table_schema = 'clockyn_dev'" in body
    assert body.index("NOT EXISTS") < body.index("ADD COLUMN") < body.index("UPDATE")


def test_a_fresh_database_has_the_columns_and_no_backfill():
    users = re.search(r"CREATE TABLE clockyn_dev\.users \((.*?)\n\);", FRESH, re.S).group(1)
    assert "welcome_email_sent_at timestamptz NULL" in users and "welcome_email_attempts int4 DEFAULT 0 NOT NULL" in users
    assert "welcome_email_sent_at = now()" not in FRESH


def test_the_env_variable_is_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`WELCOME_EMAIL_ENABLED`" in readme and "migration 042" in readme


# ═════════════════════════════════════════════════════════════════════════ the log lines an operator reads ("grep welcome-email")
def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("welcome-email:")]


def test_the_log_says_once_that_the_switch_is_off(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.delenv("WELCOME_EMAIL_ENABLED", raising=False)
    monkeypatch.setattr(we, "_warned_off", False)
    asyncio.run(we.schedule_welcome_email("admin-1"))
    asyncio.run(we.schedule_welcome_email("admin-1"))
    lines = [l for l in _lines(caplog) if "OFF" in l]
    assert len(lines) == 1 and "WELCOME_EMAIL_ENABLED" in lines[0]


def test_a_sent_mail_logs_each_step_without_the_address_or_body(env, caplog):
    caplog.set_level("INFO")
    assert we.process("admin-1") == "sent"
    text = "\n".join(_lines(caplog))
    for step in ("checking user admin-1", "claimed user admin-1", "sending to admin admin-1 via SES", "SENT for user admin-1"):
        assert step in text
    assert "asha@example.com" not in caplog.text and "<p>Hello</p>" not in caplog.text


@pytest.mark.parametrize("change,reason", [
    (lambda e: e.st.users["admin-1"].update(welcome_email_sent_at=NOW), "already sent"),
    (lambda e: e.st.users["admin-1"].update(welcome_email_attempts=3), "attempts are used up"),
    (lambda e: e.st.users["admin-1"].update(welcome_email_last_attempt_at=NOW, welcome_email_attempts=1), "less than an hour ago"),
    (lambda e: e.st.users["admin-1"].update(email="not-an-address"), "blank or not valid"),
])
def test_a_skipped_admin_is_logged_with_the_reason(env, caplog, monkeypatch, change, reason):
    caplog.set_level("INFO")
    change(env)
    u = env.st.users["admin-1"]
    recent = u["welcome_email_last_attempt_at"] is not None
    real = Cur.execute

    def execute(self, sql, params=None):                      # the fake plays DIAGNOSE_SQL from the same row the claim looked at
        if norm(sql).startswith("SELECT u.user_id, u.role_id"):
            self.row = dict(u, tried_recently=recent, email_ok="@" in u["email"] and "." in u["email"], welcome_email_attempts=u["welcome_email_attempts"])
            return None
        return real(self, sql, params)

    monkeypatch.setattr(Cur, "execute", execute)
    assert we.process("admin-1") == "skipped"
    assert any("skipped user admin-1" in l and reason in l for l in _lines(caplog))


def test_skip_reason_covers_a_manager_and_an_unknown_id():
    assert "not a company admin" in we.skip_reason({"role_id": 2})
    assert "no user" in we.skip_reason(None)


def test_a_failed_send_logs_the_step_and_the_error_but_not_the_address(env, caplog):
    caplog.set_level("INFO")
    env.stub.fail_send = True
    assert we.process("admin-1") == "failed"
    text = "\n".join(_lines(caplog))
    assert "FAILED for user admin-1 while sending through Amazon SES: RuntimeError: SES said no" in text
    assert we._safe_message(RuntimeError("Email address is not verified: asha@example.com"), {"email": "asha@example.com"}) == "Email address is not verified: <recipient>"


def test_the_home_hook_logs_when_it_does_not_hand_over(monkeypatch, caplog):
    caplog.set_level("INFO")
    asyncio.run(home._welcome_email_hook("mgr-1", "org-1", ["b1"], None))
    assert any("not checked for user mgr-1" in l for l in _lines(caplog))


def test_a_repeat_visit_is_logged_as_skipped_by_the_cache(env, caplog):
    caplog.set_level("INFO")
    we._recent["admin-1"] = __import__("time").monotonic()
    asyncio.run(we.schedule_welcome_email("admin-1"))
    assert any("looked at less than 10 minutes ago" in l for l in _lines(caplog))
