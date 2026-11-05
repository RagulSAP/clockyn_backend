"""
How users sign in, chosen per deployment with the ``USER_AUTH`` environment variable.

* ``otp``   (default) - phone number + one-time code sent by SMS (MSG91). Unchanged legacy behaviour.
* ``email``           - e-mail address + one-time code sent by e-mail (AWS SES). No SMS is ever sent and phone numbers
                        are rejected by the OTP routes. Needs ``EMAIL_USER`` (an SES-verified sender) and AWS credentials.

The value is read on every call (a restart is the only thing needed to change it) and is case-insensitive. The lower-case
spelling ``user_auth`` is accepted too. Anything else falls back to ``otp`` and logs a warning.
"""
import logging
import os

logger = logging.getLogger(__name__)

AUTH_MODE_OTP = "otp"
AUTH_MODE_EMAIL = "email"
_VALID_MODES = (AUTH_MODE_OTP, AUTH_MODE_EMAIL)

_warned_values: set = set()


def get_user_auth_mode() -> str:
    """The active sign-in mode: ``"otp"`` or ``"email"``."""
    raw = os.getenv("USER_AUTH")
    if raw is None:
        raw = os.getenv("user_auth")
    value = (raw or "").strip().lower()
    if value == "":
        return AUTH_MODE_OTP
    if value in _VALID_MODES:
        return value
    if value not in _warned_values:
        _warned_values.add(value)
        logger.warning("USER_AUTH=%r is not one of %s - using %r", raw, _VALID_MODES, AUTH_MODE_OTP)
    return AUTH_MODE_OTP


def is_email_auth() -> bool:
    return get_user_auth_mode() == AUTH_MODE_EMAIL
