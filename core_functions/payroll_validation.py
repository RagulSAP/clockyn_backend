"""Server-side validation of payroll inputs (decision D-15). Pure functions, no database, no FastAPI app.

Rules (the same bounds the app enforces on its forms, now enforced by the server too):
  * amounts: a number, 0 < x <= 9,999,999.99, at most two decimals (`allow_zero=True` for the payslip template
    fields where the app sends 0 for a blank optional amount),
  * percentages: 0 <= x <= 100, at most two decimals,
  * whole numbers (counts, minutes): integers >= 1,
  * names: non-blank, trimmed, bounded length,
  * uniqueness per organisation and type is decided by the callers (a SELECT under an advisory lock); the messages
    live here so the wording stays in one place.

Failures raise `HTTPException(400)` (bad value) with the message the app shows; duplicates are `409`.
Messages reuse the client wording where the client has one (`e2e/mock-server/routes/payroll.js`,
`src/features/payroll/utils/*`).
"""
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import HTTPException

MAX_AMOUNT = Decimal("9999999.99")      # numeric(10, 2) columns (pay_slip, wages, advance_details)
MAX_INT = 2147483647                    # int4 columns
MAX_DURATION_MINUTES = 1440             # an overtime slot is at most one day
MAX_NAME_LENGTH = 255                   # pay_slip_name varchar(255); incentive template_name is varchar(150)
MAX_INCENTIVE_NAME_LENGTH = 150
_TWO_PLACES = Decimal("0.01")

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

MSG_PERCENT_RANGE = "Enter a number between 0-100"      # bonusForm.ts PERCENT_RANGE_MESSAGE
MSG_VALID_NUMBER = "Must be a valid number"             # incentiveForms.ts INVALID
MSG_TEMPLATE_NAME_REQUIRED = "Template name is required"      # mock payroll.js
MSG_TEMPLATE_NAME_EXISTS = "A template with this name already exists"   # mock payroll.js
MSG_AMOUNT_EXISTS = "A template with this amount already exists"        # mock payroll.js / wages


def bad_request(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Finite Decimal of a number or numeric string; None for anything else (bool, list, None, NaN, text)."""
    if value is None or isinstance(value, (bool, list, dict, tuple, set)):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def parse_amount(value: Any, label: str = "Amount", allow_zero: bool = False) -> Decimal:
    """Validated amount as a Decimal with two places. 0 < x <= 9,999,999.99 (0 allowed with `allow_zero`)."""
    if allow_zero:
        invalid = bad_request(f"{label} must be a valid amount (0 or more)")
    else:
        invalid = bad_request(f"{label} must be greater than 0")
    d = _to_decimal(value)
    if d is None:
        raise invalid
    if d < 0 or (d == 0 and not allow_zero):
        raise invalid
    if d > MAX_AMOUNT:
        raise bad_request(f"{label} must not exceed 9999999.99")
    if d != d.quantize(_TWO_PLACES):
        raise bad_request(f"{label} can have at most 2 decimal places")
    return d.quantize(_TWO_PLACES)


def parse_percent(value: Any, label: str = "Percentage") -> Decimal:
    """Validated percentage 0..100 (both ends allowed), at most two decimals."""
    d = _to_decimal(value)
    if d is None or d < 0 or d > 100:
        raise bad_request(MSG_PERCENT_RANGE)
    if d != d.quantize(_TWO_PLACES):
        raise bad_request(f"{label} can have at most 2 decimal places")
    return d.quantize(_TWO_PLACES)


def parse_positive_int(value: Any, label: str = "Value", maximum: int = MAX_INT) -> int:
    """Whole number >= 1 (decimals are rejected, not truncated)."""
    if isinstance(value, bool) or value is None:
        raise bad_request(MSG_VALID_NUMBER)
    if isinstance(value, int):
        n = value
    else:
        d = _to_decimal(value)
        if d is None or d != d.to_integral_value():
            raise bad_request(MSG_VALID_NUMBER)
        n = int(d)
    if n < 1:
        raise bad_request(MSG_VALID_NUMBER)
    if n > maximum:
        raise bad_request(f"{label} must not exceed {maximum}")
    return n


def parse_duration_minutes(value: Any) -> int:
    """Overtime slot length in minutes: whole number, 1..1440."""
    return parse_positive_int(value, "Duration", MAX_DURATION_MINUTES)


def parse_name(value: Any, required_message: str, max_length: int = MAX_NAME_LENGTH) -> str:
    """Trimmed non-blank name no longer than `max_length`."""
    if not isinstance(value, str) or not value.strip():
        raise bad_request(required_message)
    name = value.strip()
    if len(name) > max_length:
        raise bad_request(f"Name must be at most {max_length} characters")
    return name


def parse_month_name(value: Any) -> str:
    """English month name (the BONUS template stores `October`, not 10). Case-insensitive input, canonical output."""
    if isinstance(value, str):
        for name in MONTH_NAMES:
            if value.strip().lower() == name.lower():
                return name
    raise bad_request("Month is required")


def parse_year(value: Any) -> int:
    """Calendar year 2000..2100."""
    d = _to_decimal(value)
    if d is None or isinstance(value, bool) or d != d.to_integral_value() or not 2000 <= int(d) <= 2100:
        raise bad_request("Year must be between 2000 and 2100")
    return int(d)


def parse_month_number(value: Any) -> int:
    """Month 1..12 for query/body parameters (422: it is a malformed request, like FastAPI's own range check)."""
    d = _to_decimal(value)
    if d is None or isinstance(value, bool) or d != d.to_integral_value() or not 1 <= int(d) <= 12:
        raise bad_request("month must be between 1 and 12", status=422)
    return int(d)


# --------------------------------------------------------------------------- payslip template
TEMPLATE_AMOUNT_FIELDS = (
    ("basic_pay", "Basic pay"),
    ("hra", "HRA"),
    ("epf", "EPF"),
    ("professional_tax", "Professional tax"),
    ("health_insurance", "Health insurance"),
)


def validate_salary_template(data) -> dict:
    """Validate a `SalaryTemplateCreate`-like object (attributes: payslip_name, basic_pay, hra, epf, professional_tax,
    health_insurance, custom_earnings, custom_deductions). Returns normalised values:
    `{name, amounts: {field: Decimal}, custom_earnings: [(name, Decimal)], custom_deductions: [(name, Decimal)]}`.

    Fixed amounts may be 0 (a blank optional amount is sent as 0 by the app) but not negative, above 9,999,999.99 or
    with more than two decimals. A custom line without a name or with a zero amount is dropped (the app never saves
    those); every other line must carry a valid amount."""
    name = parse_name(getattr(data, "payslip_name", None), MSG_TEMPLATE_NAME_REQUIRED)
    amounts = {}
    for field, label in TEMPLATE_AMOUNT_FIELDS:
        amounts[field] = parse_amount(getattr(data, field, None), label, allow_zero=True)

    def lines(items, kind):
        out = []
        for item in items or []:
            raw_name = getattr(item, "name", None)
            line_name = raw_name.strip() if isinstance(raw_name, str) else ""
            if not line_name:
                continue
            if len(line_name) > MAX_NAME_LENGTH:
                raise bad_request(f"{kind} name must be at most {MAX_NAME_LENGTH} characters")
            amount = parse_amount(getattr(item, "amount", None), f"{kind} '{line_name}'", allow_zero=True)
            if amount == 0:
                continue
            out.append((line_name, amount))
        return out

    return {
        "name": name,
        "amounts": amounts,
        "custom_earnings": lines(getattr(data, "custom_earnings", None), "Earning"),
        "custom_deductions": lines(getattr(data, "custom_deductions", None), "Deduction"),
    }
