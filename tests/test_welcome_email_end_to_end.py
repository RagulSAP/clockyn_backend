"""The welcome e-mail end to end: the REAL template and the REAL sender (only the database and SES are fakes).
The other welcome e-mail tests stub one side or the other; this one proves the two halves fit together: the first Home load of a new
admin claims the send, the template renders with the company's data, the message goes out as multipart/related with the logo inline
(Content-ID matching the cid in the HTML), the subject is the approved one, and the e-mail is sent exactly once.
Run: python -m pytest tests/test_welcome_email_end_to_end.py -q
"""
import email
import email.policy
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import welcome_email as we  # noqa: E402
from test_welcome_email import Conn, FakeSes, State  # noqa: E402


@pytest.fixture
def world(monkeypatch):
    st, ses = State(), FakeSes()
    monkeypatch.setenv("WELCOME_EMAIL_ENABLED", "1")
    monkeypatch.setenv("EMAIL_USER", "noreply@clockyn.test")
    monkeypatch.setenv("SUPPORT_EMAIL", "support@clockyn.test")
    monkeypatch.setenv("ANDROID_STORE_URL", "https://play.google.com/store/apps/details?id=com.clockyn.app")
    monkeypatch.delenv("IOS_STORE_URL", raising=False)
    monkeypatch.delenv("EMAIL_LOGO_URL", raising=False)
    monkeypatch.delenv("EMAIL_LOGO_FILE", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.setattr(we, "get_db_connection", lambda: Conn(st))
    monkeypatch.setattr(we, "return_connection", lambda c: None)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=ses.client))
    we._recent.clear()
    return types.SimpleNamespace(st=st, ses=ses)


def message(call):
    return email.message_from_bytes(call["RawMessage"]["Data"], policy=email.policy.default)


def test_a_new_admin_gets_one_complete_welcome_e_mail(world):
    assert we.process("admin-1") == "sent"
    assert len(world.ses.calls) == 1
    call = world.ses.calls[0]
    assert call["Destinations"] == ["asha@example.com"] and call["Source"] == "noreply@clockyn.test"
    msg = message(call)
    assert str(msg["Subject"]) == "Welcome to Clockyn, Asha! Your 1-month free trial has started"
    assert msg["From"] == "Clockyn <noreply@clockyn.test>" and msg["Reply-To"] == "support@clockyn.test"

    # structure: related > (alternative > text, html) + the inline logo
    assert msg.get_content_type() == "multipart/related"
    alternative, logo = list(msg.iter_parts())
    assert alternative.get_content_type() == "multipart/alternative"
    text_part, html_part = list(alternative.iter_parts())
    assert (text_part.get_content_type(), html_part.get_content_type()) == ("text/plain", "text/html")
    assert logo.get_content_type() == "image/png" and logo.get_content_disposition() == "inline"
    cid = str(logo["Content-ID"]).strip("<>")
    html, text = html_part.get_content(), text_part.get_content()
    assert f'src="cid:{cid}"' in html                                   # the HTML points at the attached image
    assert logo.get_payload(decode=True)[:8] == b"\x89PNG\r\n\x1a\n"

    # content from the database
    assert "Welcome to Clockyn," in html and "Asha" in html and "Sunrise Traders" in html
    assert "16 Dec 2026" in html and "16 Dec 2026" in text
    assert "https://play.google.com/store/apps/details?id=com.clockyn.app" in html
    assert "mailto:support@clockyn.test" in html
    assert "example.com" not in html and "<script" not in html.lower()

    # claimed and recorded
    user = world.st.users["admin-1"]
    assert user["welcome_email_sent_at"] is not None and user["welcome_email_attempts"] == 1


def test_a_second_home_load_sends_nothing_more(world):
    assert we.process("admin-1") == "sent"
    assert we.process("admin-1") == "skipped"
    assert len(world.ses.calls) == 1


def test_a_manager_never_gets_it(world):
    assert we.process("mgr-1") == "skipped"
    assert world.ses.calls == []


def test_a_hostile_company_name_is_inert_in_the_sent_message(world):
    world.st.orgs["org-1"]["org_name"] = '<script>alert(1)</script>"onmouseover="x'
    assert we.process("admin-1") == "sent"
    html = next(p for p in message(world.ses.calls[0]).walk() if p.get_content_type() == "text/html").get_content()
    assert "<script>alert(1)</script>" not in html and '"onmouseover="x' not in html
    assert "&lt;script&gt;" in html


def test_without_the_logo_file_the_mail_is_still_complete(world, monkeypatch):
    monkeypatch.setenv("EMAIL_LOGO_FILE", "no_such_logo.png")
    assert we.process("admin-1") == "sent"
    msg = message(world.ses.calls[0])
    assert msg.get_content_type() == "multipart/alternative"             # no inline image, nothing broken
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
    assert "cid:" not in html


def test_save_a_sample_message_for_a_manual_look(world, tmp_path):
    """Writes the exact message SES would receive to the pytest temp dir (open the .eml in any mail client)."""
    we.process("admin-1")
    target = tmp_path / "welcome_sample.eml"
    target.write_bytes(world.ses.calls[0]["RawMessage"]["Data"])
    assert target.stat().st_size > 20000
    assert re.search(rb"Content-ID: <[^>]+>", target.read_bytes())


def test_the_home_screen_sends_the_organisation_id_and_the_admin_still_gets_the_email(world):
    """GET /home/dashboard-status?user_id=<ORG id>: the id of an admin's Home call is the organisation's, not the admin's own user id."""
    assert world.st.users["admin-1"]["org_id"] == "org-1" and "org-1" not in world.st.users       # the two ids really differ
    assert we.process("org-1") == "sent"
    assert len(world.ses.calls) == 1 and world.ses.calls[0]["Destinations"] == ["asha@example.com"]
    user = world.st.users["admin-1"]
    assert user["welcome_email_sent_at"] is not None                                              # recorded on the ADMIN's row
    assert we.process("org-1") == "skipped" and we.process("admin-1") == "skipped"               # either id: still only once
    assert len(world.ses.calls) == 1


def test_an_organisation_id_never_reaches_a_manager(world):
    world.st.orgs["org-1"]["parent_id"] = "mgr-1"                                                  # even a wrongly linked owner: role_id must be 1
    assert we.process("org-1") == "skipped"
    assert world.ses.calls == []
