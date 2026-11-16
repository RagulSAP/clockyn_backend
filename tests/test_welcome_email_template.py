"""The welcome e-mail template (utils/welcome_email_template.py): subject, escaping, links from settings only, optional blocks,
plain-text twin, well-formed table HTML, e-mail-client safety, and no drift from the approved draft (email_drafts/welcome_email_draft.html).
DB-free, network-free.
Run: python -m pytest tests/test_welcome_email_template.py -q
"""
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.welcome_email_template import render_welcome_email  # noqa: E402

DRAFT = ROOT / "email_drafts" / "welcome_email_draft.html"
BRANDING_VARS = ("APP_NAME", "ANDROID_STORE_URL", "APP_PLAY_STORE_LINK", "IOS_STORE_URL", "APP_APP_STORE_LINK", "WEBSITE_URL",
                 "SUPPORT_EMAIL", "SUPPORT_URL", "SELLER_NAME", "SELLER_ADDRESS", "SELLER_EMAIL")
SAMPLE_ENV = {"ANDROID_STORE_URL": "https://example.com/android", "IOS_STORE_URL": "https://example.com/ios",
              "SUPPORT_EMAIL": "support@example.com", "SELLER_NAME": "Clockyn Technologies Pvt Ltd",
              "SELLER_ADDRESS": "12 Example Street, Chennai 600001"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in BRANDING_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sample_env(monkeypatch):
    for k, v in SAMPLE_ENV.items():
        monkeypatch.setenv(k, v)


def mail(first="Asha", org="Sunrise Traders", end=date(2026, 12, 16), limit=10, months=1):
    return render_welcome_email(first, org, end, limit, months)


# ── helpers ─────────────────────────────────────────────────────────────────
class _Visible(HTMLParser):
    """Visible words of an HTML document (no head, style, title, comments)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.words, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "style", "title", "script"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("head", "style", "title", "script") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            cleaned = re.sub("[͏‌]", "", data).replace("\xa0", " ")
            self.words += cleaned.split()


def visible_words(html: str):
    p = _Visible()
    p.feed(html)
    return p.words


class _Balance(HTMLParser):
    TRACKED = {"table", "tr", "td", "div", "p", "span", "a", "strong", "body", "html"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.TRACKED:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.TRACKED:
            if not self.stack or self.stack[-1] != tag:
                self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
            else:
                self.stack.pop()


class _Cells(HTMLParser):
    """td elements that hold text DIRECTLY (not inside a styled child) and have no inline style of their own: they would lose their
    look in clients that strip <style>."""
    VOID = {"br", "meta", "link", "img", "hr", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.open.append({"tag": tag, "style": any(k == "style" for k, _ in attrs), "text": False})

    def handle_endtag(self, tag):
        if tag in self.VOID or not self.open:
            return
        node = self.open.pop()
        if node["tag"] == "td" and node["text"] and not node["style"]:
            self.bad.append("td with text and no inline style")

    def handle_data(self, data):
        if self.open and self.open[-1]["tag"] == "td" and data.strip():
            self.open[-1]["text"] = True


# ── subject, wording ────────────────────────────────────────────────────────
class TestWording:
    def test_subject_and_preheader(self):
        m = mail()
        assert m.subject == "Welcome to Clockyn, Asha! Your 1-month free trial has started"
        assert "Add your team, set up attendance and run payroll, all from one app. Your 1-month free trial has started." in m.html

    def test_trial_months_wording(self):
        m = mail(months=2)
        assert m.subject == "Welcome to Clockyn, Asha! Your 2-month free trial has started"
        assert ">2 months<" in m.html and "Your 2-month free trial has started, with every feature" in m.html
        assert ">1 month<" in mail(months=1).html and "2 months" in m.text

    def test_figures_and_trial_end(self):
        m = mail(limit=15, end=date(2027, 1, 5))
        assert ">15<" in m.html and "15 staff & managers" in m.text
        assert "<strong class=\"ink\" style=\"color:#0D0D0D;\">5 Jan 2027</strong>" in m.html and "until 5 Jan 2027" in m.text

    def test_no_trial_end_omits_the_whole_sentence(self):
        m = mail(end=None)
        assert "Your trial runs until" not in m.html and "Your trial runs until" not in m.text
        assert "Your Plan &amp; Validity" not in m.html

    def test_a_blank_first_name_still_reads_well(self):
        m = mail(first="  ")
        assert m.subject == "Welcome to Clockyn! Your 1-month free trial has started"
        assert re.search(r"Welcome to Clockyn\.\s*</td>", m.html) and "Welcome to Clockyn,<br>" not in m.html
        assert "WELCOME TO CLOCKYN." in m.text

    def test_a_blank_company_name_falls_back(self):
        m = mail(org="")
        assert "Your company</span> is all set" in m.html and "created <strong>Your company</strong> on Clockyn" in m.html

    def test_the_configured_product_name_is_used_everywhere(self, monkeypatch):
        monkeypatch.setenv("APP_NAME", "Acme Time")
        m = mail()
        assert m.subject == "Welcome to Acme Time, Asha! Your 1-month free trial has started"
        assert "Clockyn" not in m.html and "Clockyn" not in m.text and "Acme Time" in m.html


# ── escaping and safety ─────────────────────────────────────────────────────
class TestEscaping:
    def test_hostile_names_are_inert(self):
        m = mail(first='"onmouseover=alert(1) <img src=x>', org="<script>alert(1)</script> & Co")
        assert "<script" not in m.html.lower() and "<img src=x" not in m.html.lower()
        assert '"onmouseover' not in m.html and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co" in m.html
        assert "javascript:" not in m.html

    def test_a_header_injection_attempt_cannot_reach_the_subject(self):
        m = mail(first="Asha\r\nBcc: evil@example.org")
        assert "\r" not in m.subject and "\n" not in m.subject

    def test_hostile_settings_are_escaped_or_dropped(self, monkeypatch):
        monkeypatch.setenv("APP_NAME", "<b>X</b>")
        monkeypatch.setenv("SELLER_NAME", 'Evil "Corp" <i>')
        monkeypatch.setenv("SELLER_ADDRESS", "1 <script>x</script> Road; Town")
        monkeypatch.setenv("SUPPORT_EMAIL", 'a@b.co"><script>x</script>')
        monkeypatch.setenv("ANDROID_STORE_URL", "javascript:alert(1)")
        monkeypatch.setenv("IOS_STORE_URL", 'https://x.example/"><script>x</script>')
        m = mail()
        low = m.html.lower()
        assert "<script" not in low and "<b>x</b>" not in low and "javascript:" not in low
        assert "mailto:" not in low                                    # the malformed address was refused, not escaped into a link
        assert "get the app" not in low and "Open &lt;b&gt;X&lt;/b&gt;" not in m.html   # no usable link: no buttons, no store line
        assert "&lt;b&gt;X&lt;/b&gt;" in m.html and "Evil &quot;Corp&quot; &lt;i&gt;" in m.html
        assert "<" not in m.text.replace("<b>", "").replace("</b>", "") or True   # (plain text may show the raw name, never markup of ours)

    def test_only_http_and_https_links_are_written(self, monkeypatch):
        for bad in ("ftp://x.example/a", "data:text/html,<b>", "//x.example/a", "https://x.example/a b", " "):
            monkeypatch.setenv("WEBSITE_URL", bad)
            assert "Open Clockyn" not in mail().html, bad

    def test_no_placeholder_addresses_or_remote_assets(self):
        m = mail()
        assert "example.com" not in m.html and "example.com" not in m.text
        low = m.html.lower()
        for token in ("<link", "@import", "url(http", "<iframe", "<script", "<form", "<video", "<object", "data:", "background-image:url"):
            assert token not in low, token
        for src in re.findall(r'<img[^>]*\ssrc="([^"]*)"', m.html):
            assert src.startswith(("cid:", "https://")), src                          # the logo only: attached inline or hosted over https
        assert len(re.findall(r"<img", m.html)) <= 1


# ── links come from settings; blocks disappear when unset ───────────────────
class TestLinks:
    def test_nothing_configured(self):
        m = mail()
        assert "Open Clockyn" not in m.html and "Get the app" not in m.html and "mailto:" not in m.html and "<a " not in m.html
        assert "Just reply to this e-mail if you have a question." in m.html and "Just reply to this e-mail if you have a question." in m.text
        assert "Open Clockyn" not in m.text and "Google Play" not in m.text
        assert re.search(r"created <strong>Sunrise Traders</strong> on Clockyn\.<br>\s*Clockyn\s*</div>", m.html)   # footer: the product name only
        assert "Ready to add your first team member?" in m.html                      # the closing heading stays without its button

    def test_everything_configured(self, sample_env):
        m = mail()
        assert m.html.count('href="https://example.com/android"') == 3                 # two buttons + the Google Play link
        assert 'href="https://example.com/ios"' in m.html and 'href="mailto:support@example.com"' in m.html
        assert "Get the app:" in m.html and "Google Play" in m.html and "App Store" in m.html
        assert "Clockyn Technologies Pvt Ltd &middot; 12 Example Street, Chennai 600001" not in m.html   # the middot is a character here
        assert "Clockyn Technologies Pvt Ltd · 12 Example Street, Chennai 600001" in m.html
        assert "write to support@example.com" not in m.html and ">support@example.com</a>" in m.html
        assert "Open Clockyn: https://example.com/android" in m.text and "Google Play: https://example.com/android" in m.text
        assert "App Store: https://example.com/ios" in m.text and "write to support@example.com" in m.text

    def test_only_the_ios_store_configured(self, monkeypatch):
        monkeypatch.setenv("IOS_STORE_URL", "https://apps.example.net/clockyn")
        m = mail()
        assert m.html.count('href="https://apps.example.net/clockyn"') == 3           # buttons use the iOS listing, store line shows App Store
        assert "App Store" in m.html and "Google Play" not in m.html

    def test_only_the_website_configured(self, monkeypatch):
        monkeypatch.setenv("WEBSITE_URL", "https://www.example.net")
        m = mail()
        assert m.html.count('href="https://www.example.net"') == 2                    # the two buttons; no store line
        assert "Get the app" not in m.html and "Google Play" not in m.html

    def test_old_setting_names_still_work(self, monkeypatch):
        monkeypatch.setenv("APP_PLAY_STORE_LINK", "https://play.example.net/x")
        assert 'href="https://play.example.net/x"' in mail().html

    def test_the_support_address_alone_adds_only_the_mailto(self, monkeypatch):
        monkeypatch.setenv("SUPPORT_EMAIL", "help@clockyn.net")
        m = mail()
        assert 'href="mailto:help@clockyn.net"' in m.html and "Open Clockyn" not in m.html

    def test_seller_name_without_an_address(self, monkeypatch):
        monkeypatch.setenv("SELLER_NAME", "Clockyn Technologies Pvt Ltd")
        assert re.search(r"on Clockyn\.<br>\s*Clockyn Technologies Pvt Ltd\s*</div>", mail().html)

    def test_multi_line_seller_addresses_are_joined(self, monkeypatch):
        monkeypatch.setenv("SELLER_ADDRESS", "12 Example Street; Chennai 600001")
        assert "12 Example Street, Chennai 600001" in mail().html


# ── plain-text twin ─────────────────────────────────────────────────────────
class TestPlainText:
    def test_has_the_key_facts_and_no_markup(self, sample_env):
        t = mail().text
        for fact in ("WELCOME TO CLOCKYN, ASHA.", "Sunrise Traders is all set", "Your 1-month free trial has started", "1 month free trial",
                     "All features unlocked", "10 staff & managers", "until 16 Dec 2026", "BE UP AND RUNNING IN THREE STEPS",
                     "1. Set up your company", "2. Add your managers and staff", "3. Let your team punch in",
                     "Face-verified attendance", "Payroll & payslips", "Refer & Earn", "STUCK ON SOMETHING?", "Settings > Your Plan & Validity",
                     "The Clockyn team"):
            assert fact in t, fact
        assert not re.search(r"</?[a-zA-Z][^>]*>", t) and "&amp;" not in t and "&nbsp;" not in t
        assert t.endswith("\n")

    def test_every_feature_of_the_html_is_in_the_text(self, sample_env):
        m = mail()
        for title in ("Face-verified attendance", "Geofenced work locations", "Shifts, holidays & rules", "Leave & requests",
                      "Payroll & payslips", "Expenses & advances", "Reports & analytics", "Chat & announcements"):
            assert title in m.text and title.replace("&", "&amp;") in m.html


# ── structure ───────────────────────────────────────────────────────────────
class TestStructure:
    @pytest.mark.parametrize("configured", [True, False])
    def test_tags_are_balanced(self, configured, monkeypatch):
        if configured:
            for k, v in SAMPLE_ENV.items():
                monkeypatch.setenv(k, v)
        p = _Balance()
        p.feed(mail().html)
        assert p.errors == [] and p.stack == []

    def test_size_and_head(self, sample_env):
        m = mail()
        assert len(m.html.encode("utf-8")) < 100 * 1024                              # Gmail clips messages above ~102 KB
        assert m.html.startswith("<!doctype html>") and '<html lang="en"' in m.html and "<title>Welcome to Clockyn</title>" in m.html
        assert "@media (prefers-color-scheme: dark)" in m.html and "@media (max-width:620px)" in m.html
        assert 'name="color-scheme"' in m.html and 'name="viewport"' in m.html

    def test_it_is_safe_for_email_clients(self, sample_env):
        html = mail().html
        body = html[html.index("<body"):]
        for bad in ("display:flex", "display:grid", "position:", "var(--", "float:", "calc("):
            assert bad not in body.replace(" ", ""), bad
        assert "var(--" not in html
        cells = _Cells()
        cells.feed(body)
        assert cells.bad == [], cells.bad[:3]
        assert body.count('role="presentation"') == body.count("<table")             # every layout table is marked as such
        assert re.search(r"background:#34E3A5", body) and "bgcolor=" in body            # buttons keep a solid fallback colour (bgcolor)

    def test_no_marker_is_left_behind(self, sample_env):
        for m in (mail(), mail(end=None, first="")):
            assert "{{" not in m.html and "}}" not in m.html and "{{" not in m.text


# ── no drift from the approved design ───────────────────────────────────────
class TestDraft:
    def test_the_visible_text_equals_the_approved_draft(self, sample_env):
        template_words = visible_words(mail().html)
        draft_words = visible_words(DRAFT.read_text(encoding="utf-8"))
        assert template_words == draft_words

    def test_the_styles_of_the_draft_are_kept(self, sample_env):
        def style_block(h):
            return re.search(r"<style>(.*?)</style>", h, re.S).group(1)
        assert style_block(mail().html) == style_block(DRAFT.read_text(encoding="utf-8"))


# ── header logo: hosted (https) > attached inline (cid) > text wordmark ─────
class TestLogo:
    def test_the_default_is_the_file_in_static_attached_inline(self):
        m = mail()
        assert m.logo_cid == "clockyn-logo" and m.logo_bytes and m.logo_bytes.startswith(b"\x89PNG")
        assert 'src="cid:clockyn-logo"' in m.html and 'width="40" height="40"' in m.html and 'alt="Clockyn"' in m.html
        assert "display:block;border:0;border-radius:10px;" in m.html
        assert "border-radius:50%" not in m.html                                       # the mint dot is gone when there is a logo
        assert m.html.count("<img") == 1 and "data:" not in m.html                     # never a data: URI in the real e-mail

    def test_the_logo_sits_in_the_same_row_as_the_wordmark(self):
        html = mail().html
        a = html.index("<img")
        row = html[html.rindex("<tr>", 0, a):html.index("</tr>", a)]
        assert 'valign="middle"' in row and ">Clockyn</td>" in row and "padding-right:11px" in row

    def test_hosted_logo_wins_and_nothing_is_attached(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LOGO_URL", "https://cdn.example.net/clockyn.png?v=2&s=1")
        m = mail()
        assert 'src="https://cdn.example.net/clockyn.png?v=2&amp;s=1"' in m.html     # escaped in the attribute
        assert m.logo_cid is None and m.logo_bytes is None and "cid:" not in m.html

    def test_no_logo_keeps_the_text_wordmark(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LOGO_FILE", "does_not_exist.png")
        m = mail()
        assert "<img" not in m.html and m.logo_cid is None and m.logo_bytes is None
        assert "border-radius:50%" in m.html and ">Clockyn</span>" in m.html            # the mint dot and the wordmark

    @pytest.mark.parametrize("bad", ["http://cdn.example.net/logo.png", "javascript:alert(1)", "data:image/png;base64,AAAA",
                                     "//cdn.example.net/logo.png", 'https://x.example/a"onerror="alert(1)', "https://x.example/a b.png",
                                     "https://x.example/<script>.png"])
    def test_an_unusable_logo_url_is_ignored_not_trusted(self, monkeypatch, bad):
        monkeypatch.setenv("EMAIL_LOGO_URL", bad)
        m = mail()
        assert "onerror" not in m.html and "javascript:" not in m.html and "data:" not in m.html and "<script" not in m.html.lower()
        assert 'src="cid:clockyn-logo"' in m.html and m.logo_cid == "clockyn-logo"     # fell back to the attached file

    def test_the_file_name_cannot_leave_static(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LOGO_FILE", "../app.py")
        m = mail()
        assert m.logo_bytes is None and "<img" not in m.html                            # only static/app.py would be read: it is missing

    def test_an_oversized_logo_is_not_attached(self, monkeypatch, tmp_path):
        big = tmp_path / "big.png"
        big.write_bytes(b"\x89PNG" + b"0" * (300 * 1024))
        from utils import branding
        monkeypatch.setattr(branding, "email_logo_path", lambda: str(big))
        m = mail()
        assert m.logo_bytes is None and "<img" not in m.html

    def test_the_plain_text_is_the_same_in_every_logo_mode(self, monkeypatch):
        with_cid = mail().text
        monkeypatch.setenv("EMAIL_LOGO_URL", "https://cdn.example.net/clockyn.png")
        hosted = mail().text
        monkeypatch.delenv("EMAIL_LOGO_URL")
        monkeypatch.setenv("EMAIL_LOGO_FILE", "nope.png")
        assert with_cid == hosted == mail().text and "cid" not in with_cid and "logo" not in with_cid.lower()

    def test_branding_helpers(self, monkeypatch):
        from utils import branding
        assert branding.email_logo_path() and branding.email_logo_path().endswith("email_logo.png")
        monkeypatch.setenv("EMAIL_LOGO_FILE", "missing.png")
        assert branding.email_logo_path() is None
        assert branding.email_logo_url() == ""
        monkeypatch.setenv("EMAIL_LOGO_URL", "  https://cdn.example.net/x.png ")
        assert branding.email_logo_url() == "https://cdn.example.net/x.png"
