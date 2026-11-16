"""The welcome e-mail sent to a company admin the first time their Home screen loads after registration.

Built from email_drafts/welcome_email_draft.html (the approved design: graphite and mint, serif headlines). Do not restyle it
here: change the draft first, get the change approved, then make the same change in TEMPLATE below.
tests/test_welcome_email_template.py compares the visible text and the style block of the draft with this template, so the two
cannot drift apart silently.

    render_welcome_email(first_name, org_name, trial_end, staff_limit, trial_months) -> WelcomeEmail(subject, html, text)

Rules
* Everything that varies is HTML-escaped (names, addresses, URLs in attributes). Only http(s) links are ever written into a href.
* Links and contact details come ONLY from utils/branding (APP_NAME, ANDROID_STORE_URL, IOS_STORE_URL, WEBSITE_URL, SUPPORT_EMAIL,
  SELLER_NAME, SELLER_ADDRESS). A block whose value is not configured is left out; nothing is invented and there is no placeholder
  address anywhere.
* No tracking pixel, no remote font or stylesheet. The header logo is either attached inline (cid:, returned in logo_bytes) or an https
  image you host (EMAIL_LOGO_URL); with neither, a text wordmark. Inline styles and tables only, with a dark-mode and a phone layout.
"""
import html as _html
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from utils import branding

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PREHEADER_PAD = ("&#847;&zwnj;&nbsp;" * 7)

TEMPLATE = """<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{{title}}</title>
<style>
  body { margin:0; padding:0; width:100% !important; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
  table { border-collapse:collapse; mso-table-lspace:0; mso-table-rspace:0; }
  img { border:0; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic; }
  a { text-decoration:none; }
  .btn:hover { opacity:.9; }
  @media (max-width:620px) {
    .container { width:100% !important; }
    .px { padding-left:24px !important; padding-right:24px !important; }
    .stack { display:block !important; width:100% !important; padding-left:0 !important; padding-right:0 !important; }
    .h1 { font-size:32px !important; line-height:38px !important; }
    .hide-sm { display:none !important; }
    .stat { border-left:0 !important; border-top:1px solid #E1DFD9 !important; padding:16px 0 !important; }
    .stat-first { border-top:0 !important; }
  }
  @media (prefers-color-scheme: dark) {
    body, .page { background:#0A0A0A !important; }
    .card { background:#141414 !important; border-color:#292929 !important; }
    .ink { color:#F5F5F5 !important; }
    .muted { color:#A3A3A3 !important; }
    .rule { border-color:#292929 !important; }
    .stat { border-color:#292929 !important; }
    .num { color:#34E3A5 !important; }
    .panel { background:#1B1B1C !important; }
    .foot { color:#8A8A8A !important; }
    .footbg { background:#0F0F0F !important; border-color:#292929 !important; }
    .btn-dark { background:#F5F5F5 !important; color:#0A0A0A !important; }
    .btn-dark-td { background:#F5F5F5 !important; }
    .link { color:#34E3A5 !important; }
  }
</style>
</head>
<body class="page" style="margin:0;padding:0;background:#F2F1EE;">

<!-- preheader: the grey preview line inboxes show next to the subject -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px;">
  {{preheader}}
  &#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="page" style="background:#F2F1EE;">
<tr><td align="center" style="padding:32px 12px 44px;">

  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

    <!-- ============ HERO (graphite) ============ -->
    <tr><td class="px" bgcolor="#141416" style="background:#141416;background-image:linear-gradient(160deg,#2A2A2E 0%,#17171A 55%,#0D0D0E 100%);border-radius:22px 22px 0 0;padding:34px 44px 46px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-size:20px;line-height:24px;font-weight:700;letter-spacing:.2px;color:#FFFFFF;">
            {{wordmark}}
          </td>
          <td align="right" class="hide-sm" style="font-size:11px;letter-spacing:2.4px;font-weight:600;color:#8E8E93;text-transform:uppercase;">Track &nbsp;&middot;&nbsp; Manage &nbsp;&middot;&nbsp; Pay</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:46px;">
        <tr><td style="font-size:11px;letter-spacing:2.6px;font-weight:700;color:#34E3A5;text-transform:uppercase;">Welcome aboard</td></tr>
        <tr><td class="h1" style="padding-top:14px;font-family:Georgia,'Times New Roman',serif;font-size:42px;line-height:48px;font-weight:400;letter-spacing:-.6px;color:#FFFFFF;">
          {{heading}}
        </td></tr>
        <tr><td style="padding-top:8px;"><div style="width:44px;height:2px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div></td></tr>
        <tr><td style="padding-top:20px;font-size:16px;line-height:26px;color:#C9C9CE;">
          <span style="color:#FFFFFF;">{{org}}</span> is all set. Your {{trial_n}} free trial has started, with every feature unlocked, so you can run your whole team from your phone.
        </td></tr>
{{hero_button}}
      </table>
    </td></tr>

    <!-- ============ BODY ============ -->
    <tr><td class="card px" style="background:#FFFFFF;padding:40px 44px 14px;border-left:1px solid #E1DFD9;border-right:1px solid #E1DFD9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">

      <!-- trial at a glance: three figures separated by hairlines -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td class="stack stat stat-first" width="33%" align="center" style="padding:6px 8px;">
            <div class="num" style="font-family:Georgia,'Times New Roman',serif;font-size:34px;line-height:38px;color:#0D0D0D;">{{trial_figure}}</div>
            <div class="muted" style="font-size:11px;letter-spacing:1.8px;font-weight:600;color:#6B6B6B;padding-top:4px;text-transform:uppercase;">Free trial</div>
          </td>
          <td class="stack stat" width="33%" align="center" style="padding:6px 8px;border-left:1px solid #E1DFD9;">
            <div class="num" style="font-family:Georgia,'Times New Roman',serif;font-size:34px;line-height:38px;color:#0D0D0D;">All</div>
            <div class="muted" style="font-size:11px;letter-spacing:1.8px;font-weight:600;color:#6B6B6B;padding-top:4px;text-transform:uppercase;">Features unlocked</div>
          </td>
          <td class="stack stat" width="33%" align="center" style="padding:6px 8px;border-left:1px solid #E1DFD9;">
            <div class="num" style="font-family:Georgia,'Times New Roman',serif;font-size:34px;line-height:38px;color:#0D0D0D;">{{staff_limit}}</div>
            <div class="muted" style="font-size:11px;letter-spacing:1.8px;font-weight:600;color:#6B6B6B;padding-top:4px;text-transform:uppercase;">Staff &amp; managers</div>
          </td>
        </tr>
      </table>
{{trial_end_block}}
      <!-- steps -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:44px;">
        <tr><td class="rule" style="border-top:1px solid #E1DFD9;padding-top:30px;">
          <div style="font-size:11px;letter-spacing:2.4px;font-weight:700;color:#0A7A4B;text-transform:uppercase;" class="link">Get started</div>
          <div class="ink" style="font-family:Georgia,'Times New Roman',serif;font-size:26px;line-height:32px;color:#0D0D0D;padding-top:8px;">Be up and running in three steps</div>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:20px;">
        <tr>
          <td width="56" valign="top" class="num" style="padding:14px 0;font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:34px;color:#0A7A4B;">01</td>
          <td class="rule" style="padding:14px 0;border-bottom:1px solid #EFEDE8;">
            <div class="ink" style="font-size:16px;font-weight:700;color:#0D0D0D;">Set up your company</div>
            <div class="muted" style="font-size:14px;line-height:22px;color:#6B6B6B;padding-top:3px;">Add your branches and departments, and mark each work location so attendance can be checked against it.</div>
          </td>
        </tr>
        <tr>
          <td width="56" valign="top" class="num" style="padding:14px 0;font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:34px;color:#0A7A4B;">02</td>
          <td class="rule" style="padding:14px 0;border-bottom:1px solid #EFEDE8;">
            <div class="ink" style="font-size:16px;font-weight:700;color:#0D0D0D;">Add your managers and staff</div>
            <div class="muted" style="font-size:14px;line-height:22px;color:#6B6B6B;padding-top:3px;">Add people one by one, pick their shift and branch, and choose what each manager is allowed to do.</div>
          </td>
        </tr>
        <tr>
          <td width="56" valign="top" class="num" style="padding:14px 0;font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:34px;color:#0A7A4B;">03</td>
          <td style="padding:14px 0;">
            <div class="ink" style="font-size:16px;font-weight:700;color:#0D0D0D;">Let your team punch in</div>
            <div class="muted" style="font-size:14px;line-height:22px;color:#6B6B6B;padding-top:3px;">Each person registers their face once, then punches in from the work location. You see who is in, late or absent, live.</div>
          </td>
        </tr>
      </table>

      <!-- features -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:38px;">
        <tr><td class="rule" style="border-top:1px solid #E1DFD9;padding-top:30px;">
          <div class="link" style="font-size:11px;letter-spacing:2.4px;font-weight:700;color:#0A7A4B;text-transform:uppercase;">What is inside</div>
          <div class="ink" style="font-family:Georgia,'Times New Roman',serif;font-size:26px;line-height:32px;color:#0D0D0D;padding-top:8px;">Everything your team needs, in one app</div>
          <div class="muted" style="font-size:15px;line-height:23px;color:#6B6B6B;padding-top:6px;">From the first punch to the last payslip.</div>
        </td></tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">
        <tr>
          <td class="stack" width="50%" valign="top" style="padding:14px 14px 14px 0;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Face-verified attendance</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">A quick selfie punches in and out, so every punch is tied to the person who made it.</div>
          </td>
          <td class="stack" width="50%" valign="top" style="padding:14px 0 14px 14px;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Geofenced work locations</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Staff can punch in only where they should be, at the office or on a site you set.</div>
          </td>
        </tr>
        <tr>
          <td class="stack" width="50%" valign="top" style="padding:14px 14px 14px 0;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Shifts, holidays &amp; rules</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Set shifts, holidays and late-coming rules once. {{app}} applies them every day.</div>
          </td>
          <td class="stack" width="50%" valign="top" style="padding:14px 0 14px 14px;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Leave &amp; requests</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Staff ask from the app and you approve in one tap, with everything on record.</div>
          </td>
        </tr>
        <tr>
          <td class="stack" width="50%" valign="top" style="padding:14px 14px 14px 0;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Payroll &amp; payslips</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Attendance flows straight into salary, with overtime, deductions and advances worked out.</div>
          </td>
          <td class="stack" width="50%" valign="top" style="padding:14px 0 14px 14px;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Expenses &amp; advances</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Claims and salary advances are raised, approved and tracked in the app.</div>
          </td>
        </tr>
        <tr>
          <td class="stack" width="50%" valign="top" style="padding:14px 14px 14px 0;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Reports &amp; analytics</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Daily and monthly attendance, late and absent trends, ready to download.</div>
          </td>
          <td class="stack" width="50%" valign="top" style="padding:14px 0 14px 14px;">
            <div style="width:22px;height:3px;background:#34E3A5;font-size:0;line-height:0;">&nbsp;</div>
            <div class="ink" style="font-size:15px;font-weight:700;color:#0D0D0D;padding-top:10px;">Chat &amp; announcements</div>
            <div class="muted" style="font-size:13px;line-height:21px;color:#6B6B6B;padding-top:4px;">Message your team, or announce to everyone, without leaving the app.</div>
          </td>
        </tr>
      </table>

      <p class="muted" style="margin:14px 0 0;font-size:14px;line-height:22px;color:#6B6B6B;text-align:center;">
        Plus <strong class="ink" style="color:#0D0D0D;">incentives</strong>, <strong class="ink" style="color:#0D0D0D;">CRM &amp; leads</strong> for field teams, and <strong class="ink" style="color:#0D0D0D;">Refer &amp; Earn</strong> to save on your plan when you invite other companies.
      </p>

      <!-- help: a graphite panel that echoes the hero -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="panel" style="margin-top:38px;background:#141416;background-image:linear-gradient(160deg,#2A2A2E 0%,#17171A 60%,#0D0D0E 100%);border-radius:18px;border-collapse:separate;border:0;">
        <tr><td style="padding:28px 30px 26px;">
          <div style="font-size:11px;letter-spacing:2.4px;font-weight:700;color:#34E3A5;text-transform:uppercase;">We are here</div>
          <div style="font-family:Georgia,'Times New Roman',serif;font-size:23px;line-height:30px;color:#FFFFFF;padding-top:8px;">Stuck on something? We have you covered.</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">
            <tr><td valign="top" width="22" style="font-size:14px;line-height:23px;color:#34E3A5;padding:4px 0;">&#10003;</td><td style="font-size:14px;line-height:23px;color:#C9C9CE;padding:4px 0;"><strong style="color:#FFFFFF;">FAQ</strong>: hundreds of answers under Support in the app.</td></tr>
            <tr><td valign="top" width="22" style="font-size:14px;line-height:23px;color:#34E3A5;padding:4px 0;">&#10003;</td><td style="font-size:14px;line-height:23px;color:#C9C9CE;padding:4px 0;"><strong style="color:#FFFFFF;">Video Hub</strong>: short guides that show each step.</td></tr>
            <tr><td valign="top" width="22" style="font-size:14px;line-height:23px;color:#34E3A5;padding:4px 0;">&#10003;</td><td style="font-size:14px;line-height:23px;color:#C9C9CE;padding:4px 0;"><strong style="color:#FFFFFF;">Live Chat</strong>: talk to the {{app}} team from Support &rsaquo; Live Chat.</td></tr>
          </table>
        </td></tr>
      </table>

      <!-- closing CTA -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:40px;">
        <tr><td align="center">
          <div class="ink" style="font-family:Georgia,'Times New Roman',serif;font-size:24px;line-height:31px;color:#0D0D0D;">Ready to add your first team member?</div>
        </td></tr>
{{cta_button}}
{{store_line}}{{cta_spacer}}
      </table>

    </td></tr>

    <!-- ============ SIGN-OFF ============ -->
    <tr><td class="card px" style="background:#FFFFFF;border-left:1px solid #E1DFD9;border-right:1px solid #E1DFD9;border-top:1px solid #EFEDE8;padding:24px 44px 30px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
      <div class="ink" style="font-family:Georgia,'Times New Roman',serif;font-size:17px;line-height:26px;color:#0D0D0D;">Welcome to the team,<br><span style="font-style:italic;">The {{app}} team</span></div>
      {{support_line}}
    </td></tr>

    <!-- ============ FOOTER ============ -->
    <tr><td class="px footbg" style="background:#ECEAE5;border:1px solid #E1DFD9;border-top:0;border-radius:0 0 22px 22px;padding:22px 44px 26px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
      <div class="foot" style="font-size:12px;line-height:19px;color:#77756E;text-align:center;">
        You are receiving this one-time welcome e-mail because you created <strong>{{org}}</strong> on {{app}}.<br>
        {{footer_entity}}
      </div>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>
"""

_HERO_BUTTON = """        <tr><td style="padding-top:28px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="#34E3A5" style="border-radius:999px;background:#34E3A5;">
              <a class="btn" href="{{href}}" style="display:inline-block;padding:15px 34px;font-size:15px;font-weight:700;letter-spacing:.2px;color:#0A0A0A;border-radius:999px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">Open {{app}}</a>
            </td>
          </tr></table>
        </td></tr>
"""
_TRIAL_END = """      <p class="muted" style="margin:16px 0 0;font-size:13px;line-height:20px;color:#6B6B6B;text-align:center;">Your trial runs until <strong class="ink" style="color:#0D0D0D;">{{trial_end}}</strong>. You can check it any time under Settings &rsaquo; Your Plan &amp; Validity.</p>
"""
_CTA_BUTTON = """        <tr><td align="center" style="padding:18px 0 6px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td class="btn-dark-td" bgcolor="#0D0D0D" style="border-radius:999px;background:#0D0D0D;">
              <a class="btn btn-dark" href="{{href}}" style="display:inline-block;padding:15px 36px;font-size:15px;font-weight:700;letter-spacing:.2px;color:#FFFFFF;border-radius:999px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">Open {{app}}</a>
            </td>
          </tr></table>
        </td></tr>
"""
_STORE_LINE = """        <tr><td align="center" class="muted" style="padding:10px 0 30px;font-size:13px;line-height:20px;color:#6B6B6B;">
          Get the app: {{store_links}}
        </td></tr>
"""
_STORE_LINK = """<a class="link" href="{{href}}" style="color:#0A7A4B;font-weight:700;">{{label}}</a>"""
_SUPPORT_WITH_EMAIL = """<div class="muted" style="font-size:13px;line-height:20px;color:#6B6B6B;padding-top:10px;">Just reply to this e-mail or write to <a class="link" href="mailto:{{href}}" style="color:#0A7A4B;font-weight:700;">{{email}}</a> if you have a question.</div>"""
_SUPPORT_WITHOUT_EMAIL = """<div class="muted" style="font-size:13px;line-height:20px;color:#6B6B6B;padding-top:10px;">Just reply to this e-mail if you have a question.</div>"""
_CTA_SPACER = """        <tr><td style="padding-bottom:24px;font-size:0;line-height:0;">&nbsp;</td></tr>
"""
_WORDMARK_DOT = """<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#34E3A5;vertical-align:middle;margin-right:9px;"></span><span style="vertical-align:middle;">{{app}}</span>"""
_WORDMARK_LOGO = """<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
              <td valign="middle" style="padding-right:11px;"><img src="{{logo_src}}" width="40" height="40" alt="{{app}}" style="display:block;border:0;border-radius:10px;"></td>
              <td valign="middle" style="font-size:20px;line-height:24px;font-weight:700;letter-spacing:.2px;color:#FFFFFF;">{{app}}</td>
            </tr></table>"""

LOGO_CID = "clockyn-logo"
MAX_LOGO_BYTES = 256 * 1024


@dataclass(frozen=True)
class WelcomeEmail:
    subject: str
    html: str
    text: str
    logo_cid: Optional[str] = None        # set when the HTML points at cid:<logo_cid>: the sender must attach logo_bytes inline
    logo_bytes: Optional[bytes] = None    # the PNG to attach inline (None when the logo is hosted, or there is none)


# ── small helpers ───────────────────────────────────────────────────────────
def _e(value) -> str:
    """HTML-escape text for element content and for quoted attribute values."""
    return _html.escape(str(value if value is not None else ""), quote=True)


def _one_line(value, limit: int = 80) -> str:
    """Plain text on one line (no control characters, so it can never break a header), at most ``limit`` characters."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit].strip()


def _safe_url(url: Optional[str]) -> str:
    """The URL when it is a plain http(s) link, else empty (a javascript: or data: link is never written)."""
    value = (url or "").strip()
    if not re.match(r"^https?://[^\s<>\"']+$", value, re.I):
        return ""
    return value


def _safe_email(address: Optional[str]) -> str:
    value = (address or "").strip()
    return value if re.match(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$", value) else ""


def _safe_https_url(url: Optional[str]) -> str:
    """Hosted images must be https (an http image is blocked or flagged by mail clients)."""
    value = _safe_url(url)
    return value if value.lower().startswith("https://") else ""


def _resolve_logo():
    """(img src, cid, bytes) of the header logo, in this order: EMAIL_LOGO_URL (https only, hosted, nothing attached), then
    static/<EMAIL_LOGO_FILE> attached inline as cid:clockyn-logo, else ("", None, None) and the text wordmark is shown."""
    hosted = _safe_https_url(branding.email_logo_url())
    if hosted:
        return hosted, None, None
    path = branding.email_logo_path()
    if path:
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_LOGO_BYTES + 1)
        except OSError:
            data = b""
        if data and len(data) <= MAX_LOGO_BYTES:
            return f"cid:{LOGO_CID}", LOGO_CID, data
    return "", None, None


def _fill(template: str, values: Dict[str, str]) -> str:
    """Replace {{key}} with an already escaped value in ONE pass (a value is never scanned again)."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def format_trial_end(value: Optional[date]) -> str:
    """16 Dec 2026 (no locale: the month names are fixed)."""
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}" if value else ""


def _months_phrase(months: int) -> str:
    return "1 month" if months == 1 else f"{months} months"


# ── the e-mail ──────────────────────────────────────────────────────────────
def render_welcome_email(first_name: str, org_name: str, trial_end: Optional[date], staff_limit: int, trial_months: int) -> WelcomeEmail:
    app = _one_line(branding.app_name(), 60) or "Clockyn"
    first = _one_line(first_name, 50)
    org = _one_line(org_name, 120)
    months = int(trial_months) if trial_months and int(trial_months) > 0 else 1
    trial_n = f"{months}-month"
    figure = _months_phrase(months)
    limit = int(staff_limit) if staff_limit and int(staff_limit) > 0 else 10

    # links and contact details: settings only
    app_link = _safe_url(branding.app_download_link())
    android, ios = _safe_url(branding.android_store_url()), _safe_url(branding.ios_store_url())
    support = _safe_email(branding.support_email())
    entity = " \u00b7 ".join(p for p in [_one_line(branding.seller_name(), 120) or app,
                                         ", ".join(_one_line(x, 160) for x in branding.seller_address_lines())] if p)

    subject = f"Welcome to {app}, {first}! Your {trial_n} free trial has started" if first else \
        f"Welcome to {app}! Your {trial_n} free trial has started"
    preheader = f"Add your team, set up attendance and run payroll, all from one app. Your {trial_n} free trial has started."
    end_text = format_trial_end(trial_end)

    store_links = []
    if android:
        store_links.append(_fill(_STORE_LINK, {"href": _e(android), "label": "Google Play"}))
    if ios:
        store_links.append(_fill(_STORE_LINK, {"href": _e(ios), "label": "App Store"}))
    store_html = _fill(_STORE_LINE, {"store_links": " &nbsp;&middot;&nbsp; ".join(store_links)}) if store_links else ""

    logo_src, logo_cid, logo_bytes = _resolve_logo()
    values = {
        "title": _e(f"Welcome to {app}"),
        "wordmark": _fill(_WORDMARK_LOGO, {"logo_src": _e(logo_src), "app": _e(app)}) if logo_src else _fill(_WORDMARK_DOT, {"app": _e(app)}),
        "preheader": _e(preheader) + "\n  " + _PREHEADER_PAD,
        "app": _e(app),
        "heading": f"Welcome to {_e(app)},<br>{_e(first)}." if first else f"Welcome to {_e(app)}.",
        "org": _e(org or "Your company"),
        "trial_n": _e(trial_n),
        "trial_figure": _e(figure),
        "staff_limit": _e(limit),
        "trial_end_block": _fill(_TRIAL_END, {"trial_end": _e(end_text)}) if end_text else "",
        "hero_button": _fill(_HERO_BUTTON, {"href": _e(app_link), "app": _e(app)}) if app_link else "",
        "cta_button": _fill(_CTA_BUTTON, {"href": _e(app_link), "app": _e(app)}) if app_link else "",
        "store_line": store_html,
        "cta_spacer": "" if store_html else _CTA_SPACER,
        "support_line": _fill(_SUPPORT_WITH_EMAIL, {"href": _e(support), "email": _e(support)}) if support else _SUPPORT_WITHOUT_EMAIL,
        "footer_entity": _e(entity),
    }
    body = _fill(TEMPLATE, values)          # one pass: text that came from a user is never scanned for markers again
    return WelcomeEmail(subject=_one_line(subject, 200), html=body, logo_cid=logo_cid, logo_bytes=logo_bytes, text=_plain_text(
        app=app, first=first, org=org or "Your company", trial_n=trial_n, figure=figure, limit=limit, end_text=end_text,
        app_link=app_link, android=android, ios=ios, support=support, entity=entity))


# ── plain-text alternative (same content, no markup) ────────────────────────
def _plain_text(*, app, first, org, trial_n, figure, limit, end_text, app_link, android, ios, support, entity) -> str:
    out: List[str] = []
    add = out.append
    add((f"WELCOME TO {app.upper()}, {first.upper()}." if first else f"WELCOME TO {app.upper()}."))
    add("")
    add(f"{org} is all set. Your {trial_n} free trial has started, with every feature unlocked, so you can run your whole team from your phone.")
    if app_link:
        add("")
        add(f"Open {app}: {app_link}")
    add("")
    add("YOUR TRIAL AT A GLANCE")
    add(f"- {figure} free trial")
    add("- All features unlocked")
    add(f"- {limit} staff & managers")
    if end_text:
        add(f"Your trial runs until {end_text}. You can check it any time under Settings > Your Plan & Validity.")
    add("")
    add("BE UP AND RUNNING IN THREE STEPS")
    add("1. Set up your company: add your branches and departments, and mark each work location so attendance can be checked against it.")
    add("2. Add your managers and staff: add people one by one, pick their shift and branch, and choose what each manager is allowed to do.")
    add("3. Let your team punch in: each person registers their face once, then punches in from the work location. You see who is in, late or absent, live.")
    add("")
    add("EVERYTHING YOUR TEAM NEEDS, IN ONE APP")
    add("From the first punch to the last payslip.")
    add("- Face-verified attendance: a quick selfie punches in and out, so every punch is tied to the person who made it.")
    add("- Geofenced work locations: staff can punch in only where they should be, at the office or on a site you set.")
    add("- Shifts, holidays & rules: set shifts, holidays and late-coming rules once. " + app + " applies them every day.")
    add("- Leave & requests: staff ask from the app and you approve in one tap, with everything on record.")
    add("- Payroll & payslips: attendance flows straight into salary, with overtime, deductions and advances worked out.")
    add("- Expenses & advances: claims and salary advances are raised, approved and tracked in the app.")
    add("- Reports & analytics: daily and monthly attendance, late and absent trends, ready to download.")
    add("- Chat & announcements: message your team, or announce to everyone, without leaving the app.")
    add("Plus incentives, CRM & leads for field teams, and Refer & Earn to save on your plan when you invite other companies.")
    add("")
    add("STUCK ON SOMETHING? WE HAVE YOU COVERED.")
    add("- FAQ: hundreds of answers under Support in the app.")
    add("- Video Hub: short guides that show each step.")
    add(f"- Live Chat: talk to the {app} team from Support > Live Chat.")
    add("")
    add("READY TO ADD YOUR FIRST TEAM MEMBER?")
    if app_link:
        add(f"Open {app}: {app_link}")
    if android:
        add(f"Google Play: {android}")
    if ios:
        add(f"App Store: {ios}")
    add("")
    add("Welcome to the team,")
    add(f"The {app} team")
    add(f"Just reply to this e-mail or write to {support} if you have a question." if support else "Just reply to this e-mail if you have a question.")
    add("")
    add("--")
    add(f"You are receiving this one-time welcome e-mail because you created {org} on {app}.")
    add(entity)
    return "\n".join(out) + "\n"
