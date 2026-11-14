"""Pure pricing, validity and GST logic of the plan purchase flow (no database, no network, no clock).

The server is the only authority on what a checkout costs: ``POST /orders/quote`` and ``POST /orders/create`` both
call :func:`build_quote` with prices read from ``gen_plans`` / ``gen_coupons`` and the organisation's subscription.
A price, plan amount or coupon discount sent by the client is never used.

The rounding rules reproduce the app's own maths (``src/features/billing/utils/pricing.ts`` + ``quote.ts``) so the
numbers on the checkout screen and the amount charged are identical:

    staff_charge = staff x price_per_staff_per_month x months                (exact, not rounded)
    after_credit = max(0, staff_charge - credit)
    discount     = whole rupee, half up (percent of after_credit, or a flat value, capped by max_discount)
    subtotal     = max(0, after_credit - discount)
    gst          = whole rupee, half up (18 % of subtotal)
    grand_total  = subtotal + gst

``Decimal`` with ``ROUND_HALF_UP`` equals JS ``Math.round`` for the non-negative values involved; Python's ``round()``
(half-even) must not be used. Amounts are ``Decimal`` with two places.

Three purchase modes (the app's ``PurchaseMode``):

    new      buy / change plan: charged for the plan's own duration less its free months (a 6 month row with 1 free month
             charges 5 and is valid for 6), minus a credit for the unexpired current plan
             (10 staff x current price x current PAID months), term starts today.
    renew    extend the CURRENT plan by 12 months (priced at the current tier, with the same share of free months:
             6 months / 1 free renews as 12 months / 2 free): validity runs from the current end date
             while the plan is still active, from today once it has expired.
    upgrade  add staff capacity to the running term: only the EXTRA staff are charged, for the whole calendar months
             left (day of month ignored, at least 1); no credit, no coupon; the end date does not change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, Optional, Sequence, Tuple

from dateutil.relativedelta import relativedelta

from utils import branding

D = Decimal
ZERO = D("0.00")
_CENT = D("0.01")
_RUPEE = D("1")

MODES: Tuple[str, ...] = ("new", "upgrade", "renew")

MIN_STAFF = 10                 # smallest capacity that can be bought (client: MIN_STAFF)
TRIAL_STAFF_LIMIT = 10         # D-13: staff limit while on the free trial / without an active plan
MAX_STAFF = 10000              # sanity cap against absurd values
CREDIT_BASELINE_STAFF = 10     # credit for the current plan assumes this many staff (client: CREDIT_BASELINE_STAFF)
RENEW_MONTHS = 12              # renewals are always priced for 12 months
GST_PERCENT = D("18")

# Refer & Earn: the ONE place the reward size and the plans it works on are defined (Prime = 2, Elite = 3; Core = 1 is not eligible).
REFERRAL_PERCENT = 10
REFERRAL_ELIGIBLE_PLAN_IDS: Tuple[int, ...] = (2, 3)
REFERRAL_MODES: Tuple[str, ...] = ("new", "renew")      # `upgrade` (extra staff) is never eligible
REFERRAL_MSG_PLAN = "Referral rewards are valid on Prime and Elite plans only"
REFERRAL_MSG_UPGRADE = "Referral rewards do not apply to staff upgrades"
REFERRAL_MSG_NONE = "No referral reward available"
REFERRAL_MSG_WITH_COUPON = "Use either a coupon or a referral reward, not both"


class PricingError(Exception):
    """A request that cannot be priced; ``status`` is the HTTP status the API answers with."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class CouponError(PricingError):
    """The coupon exists in the request but cannot be applied (the message is shown to the user)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, 400)


# ── numbers ─────────────────────────────────────────────────────────────────
def to_decimal(value: Any, default: Optional[Decimal] = None) -> Decimal:
    """Decimal from int / float / str / Decimal without binary-float noise. Raises PricingError on junk when no default."""
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        if default is not None:
            return default
        raise PricingError("invalid_number", "Invalid number")
    try:
        return D(str(value).strip())
    except (InvalidOperation, ValueError):
        if default is not None:
            return default
        raise PricingError("invalid_number", "Invalid number")


def money(value: Any) -> Decimal:
    """Two decimal places, half up."""
    return to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def whole_rupee(value: Any) -> Decimal:
    """Round to a whole rupee, half up (the client's Math.round), returned with two places (e.g. 1296.00)."""
    return to_decimal(value).quantize(_RUPEE, rounding=ROUND_HALF_UP).quantize(_CENT)


def to_paise(amount: Any) -> int:
    """Rupees to integer paise without float truncation (int(19.99 * 100) would be 1998)."""
    return int((to_decimal(amount) * 100).quantize(_RUPEE, rounding=ROUND_HALF_UP))


def add_months(start: date, months: int) -> date:
    """start + months, day clamped to the target month's length (31 Jan + 1 month = 28/29 Feb), like the client."""
    return start + relativedelta(months=int(months))


def paid_months(months: Any, free_months: Any = 0) -> int:
    """Months charged for a plan of ``months`` that gives ``free_months`` away. The free months are clamped to
    0..months-1, so a plan always charges at least one month and a bad row can never price a purchase at zero."""
    months = int(months or 0)
    free = max(0, min(int(free_months or 0), months - 1)) if months > 0 else 0
    return months - free


def free_months_for_term(tier: "PlanTier", term: int) -> int:
    """Free months of a purchase of ``term`` months at ``tier``'s rate. The plan's own duration gets its free months;
    a renewal (always 12 months) gets the same share, rounded down: 6 months with 1 free renews as 12 months with 2 free."""
    term = int(term)
    if tier.months <= 0:
        return 0
    free = tier.months - paid_months(tier.months, tier.free_months)
    return (term * free) // tier.months          # == free for term == tier.months


# Core / Prime / Elite are the product tiers; every duration row of a tier (1, 6, 12 months ...) shares its name and
# therefore its feature set, coupons and referral rules. The three base rows keep these ids.
PLAN_FAMILY_IDS: Dict[str, int] = {"core": 1, "prime": 2, "elite": 3}


def plan_family_id(name: Any, plan_id: Any) -> int:
    """The tier id (1 Core, 2 Prime, 3 Elite) of a plan row, by name; a name that is not one of them keeps its own id."""
    return PLAN_FAMILY_IDS.get(str(name or "").strip().lower(), int(plan_id))


def calendar_months_left(today: date, end_date: date) -> int:
    """Whole calendar months from today's month to the end date's month (day ignored), at least 1."""
    return max(1, (end_date.year - today.year) * 12 + (end_date.month - today.month))


def _as_float(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


def _percent_out(value: Decimal):
    """10 for a whole percent (JSON `10`, not `10.0`), else a float."""
    return int(value) if value == value.to_integral_value() else float(value)


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


# ── inputs ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PlanTier:
    """One ``gen_plans`` row: ``price`` is INR per staff per month (the list price, the same on every duration of a tier),
    ``months`` the plan's own duration and ``free_months`` how many of them are not charged."""

    plan_id: int
    name: str
    price: Decimal
    months: int
    free_months: int = 0         # months of ``months`` given free (a 6 month row with 1 free month charges 5)

    @property
    def paid_months(self) -> int:
        """Months that are charged for this row's own duration."""
        return paid_months(self.months, self.free_months)

    @property
    def family_id(self) -> int:
        return plan_family_id(self.name, self.plan_id)


@dataclass(frozen=True)
class CurrentSubscription:
    """The organisation's latest paid subscription row (ACTIVE or EXPIRED) with its plan tier."""

    plan: PlanTier
    status: str
    start_date: Optional[date]
    end_date: date
    staff_limit: int

    def is_expired(self, today: date) -> bool:
        # The end date itself is still valid (inclusive, IST calendar day).
        return str(self.status or "").upper() == "EXPIRED" or self.end_date < today


@dataclass(frozen=True)
class Coupon:
    """A ``gen_coupons`` row. ``discount`` is a percent for ``percent`` coupons; ``flat_amount`` is INR for ``flat``."""

    name: str
    discount: Decimal = ZERO
    discount_type: str = "percent"
    flat_amount: Optional[Decimal] = None
    max_discount: Optional[Decimal] = None
    min_amount: Optional[Decimal] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None          # inclusive
    plan_ids: Tuple[int, ...] = ()              # empty = valid for every plan
    status: int = 1
    coupon_id: Optional[int] = None

    def discount_for(self, amount: Decimal, plan_id: int, today: date, family_id: Optional[int] = None) -> Decimal:
        """Discount in whole rupees for ``amount`` (the staff charge after credit); raises CouponError when unusable.
        ``plan_ids`` may name the exact plan row or its tier (``family_id``: 2 = every Prime duration)."""
        if int(self.status or 0) != 1:
            raise CouponError("coupon_invalid", "Invalid or expired coupon")
        if self.valid_from and today < self.valid_from:
            raise CouponError("coupon_not_started", "This coupon is not active yet")
        if self.valid_until and today > self.valid_until:
            raise CouponError("coupon_expired", "Invalid or expired coupon")
        allowed = {int(p) for p in self.plan_ids}
        if allowed and int(plan_id) not in allowed and (family_id is None or int(family_id) not in allowed):
            raise CouponError("coupon_wrong_plan", "This coupon is not valid for the selected plan")
        if self.min_amount is not None and amount < self.min_amount:
            raise CouponError("coupon_min_amount", f"This coupon needs a minimum order of Rs {whole_rupee(self.min_amount):,.0f}")
        if (self.discount_type or "percent") == "flat":
            value = self.flat_amount if self.flat_amount is not None else ZERO
            if value <= 0:
                raise CouponError("coupon_invalid", "Invalid or expired coupon")
            discount = whole_rupee(value)
        else:
            pct = self.discount
            if pct <= 0 or pct > 100:
                raise CouponError("coupon_invalid", "Invalid or expired coupon")
            discount = whole_rupee(amount * pct / 100)
        if self.max_discount is not None:
            discount = min(discount, whole_rupee(self.max_discount))
        return max(ZERO, min(discount, amount))     # never below 0, never more than the amount


@dataclass(frozen=True)
class ReferralOffer:
    """What the database says about the organisation's referral rewards: how many are AVAILABLE and the one that would be
    used (the oldest). ``reward_id`` is None when there is none."""

    available_count: int = 0
    reward_id: Optional[int] = None
    percent: Decimal = D(REFERRAL_PERCENT)


# ── output ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Quote:
    mode: str
    plan_id: int
    plan_name: str
    price_per_staff_month: Decimal
    charged_staff: int           # staff charged (the EXTRA staff for an upgrade)
    charged_months: int
    term_months: int             # months added to the validity at activation (0 for an upgrade)
    staff_charge: Decimal
    credit: Decimal
    after_credit: Decimal
    coupon_code: Optional[str]
    coupon_applied: bool
    coupon_message: Optional[str]
    discount_percent: Optional[Decimal]
    discount_amount: Decimal
    subtotal: Decimal
    gst_percent: Decimal
    gst: Decimal
    grand_total: Decimal
    valid_from: date
    valid_till: date
    resulting_staff_limit: int
    min_staff: int
    has_current_plan: bool
    is_plan_expired: bool
    # Refer & Earn (always reported; the reward id is internal and never part of ``to_dict``)
    referral_applied: bool = False
    referral_percent: Decimal = D(REFERRAL_PERCENT)
    referral_message: Optional[str] = None            # why a REQUESTED reward could not be used (non-strict quote)
    referral_available_count: int = 0
    referral_eligible: bool = False                   # for THIS quote (plan + mode + a reward exists)
    referral_eligible_message: Optional[str] = None   # why not eligible
    referral_reward_id: Optional[int] = None          # the reward priced in (only when referral_applied)
    free_months: int = 0                              # months of ``term_months`` that are free (charged_months = term - free)

    @property
    def payable(self) -> bool:
        return self.grand_total > 0

    @property
    def amount_paise(self) -> int:
        return to_paise(self.grand_total)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "plan_id": self.plan_id,
            "plan_name": self.plan_name,
            "price_per_staff_month": _as_float(self.price_per_staff_month),
            "charged_staff": self.charged_staff,
            "charged_months": self.charged_months,
            "term_months": self.term_months,
            "free_months": self.free_months,
            "staff_charge": _as_float(self.staff_charge),
            "credit": _as_float(self.credit),
            "after_credit": _as_float(self.after_credit),
            "coupon_code": self.coupon_code,
            "coupon_applied": self.coupon_applied,
            "coupon_message": self.coupon_message,
            "discount_percent": _as_float(self.discount_percent),
            "discount_amount": _as_float(self.discount_amount),
            "subtotal": _as_float(self.subtotal),
            "gst_percent": _as_float(self.gst_percent),
            "gst": _as_float(self.gst),
            "grand_total": _as_float(self.grand_total),
            "amount_paise": self.amount_paise,
            "currency": "INR",
            "valid_from": _iso(self.valid_from),
            "valid_till": _iso(self.valid_till),
            "resulting_staff_limit": self.resulting_staff_limit,
            "min_staff": self.min_staff,
            "has_current_plan": self.has_current_plan,
            "is_plan_expired": self.is_plan_expired,
            "payable": self.payable,
            "referral_applied": self.referral_applied,
            "referral_percent": _percent_out(self.referral_percent),
            "referral_message": self.referral_message,
            "referral": {
                "available_count": self.referral_available_count,
                "eligible": self.referral_eligible,
                "percent": _percent_out(self.referral_percent),
                "message": self.referral_eligible_message,
            },
        }


# ── the calculation ─────────────────────────────────────────────────────────
def _validate_staff(staff: Any) -> int:
    if isinstance(staff, bool):
        raise PricingError("invalid_staff", "Staff count must be a whole number")
    try:
        value = int(staff)
        if isinstance(staff, float) and staff != value:
            raise ValueError
    except (TypeError, ValueError):
        raise PricingError("invalid_staff", "Staff count must be a whole number")
    if value < 1:
        raise PricingError("invalid_staff", "Staff count must be at least 1")
    if value > MAX_STAFF:
        raise PricingError("invalid_staff", f"Staff count cannot exceed {MAX_STAFF}")
    return value


def build_quote(
    *,
    mode: str,
    target: Optional[PlanTier],
    staff: Any,
    current: Optional[CurrentSubscription],
    today: date,
    coupon: Optional[Coupon] = None,
    coupon_code: Optional[str] = None,
    strict_coupon: bool = True,
    referral: Optional[ReferralOffer] = None,
    apply_referral: bool = False,
    strict_referral: bool = True,
) -> Quote:
    """Price one purchase.

    ``target`` is the catalogue tier chosen for ``new``; for ``renew`` / ``upgrade`` the CURRENT plan is used and
    ``target`` is ignored. ``staff`` is the stepper value (for ``upgrade`` the EXTRA staff, as the app posts it).
    ``coupon_code`` set while ``coupon`` is None means the code does not exist. With ``strict_coupon=False`` an
    unusable coupon is reported in the quote (and ignored) instead of raising :class:`CouponError`.

    ``referral`` describes the organisation's rewards (None = none). ``apply_referral`` asks for the reward to be used: when
    it cannot be (wrong mode / plan, none available) a strict quote raises ``PricingError('referral_not_applicable', ..., 400)``
    and a non-strict one reports the reason in ``referral_message``. A coupon together with ``apply_referral`` always raises
    ``PricingError('referral_with_coupon', ..., 400)``.
    """
    if mode not in MODES:
        raise PricingError("invalid_mode", "mode must be one of new, upgrade, renew")
    staff = _validate_staff(staff)
    code = (coupon_code or (coupon.name if coupon else "") or "").strip().upper() or None
    has_current = current is not None
    expired = bool(current and current.is_expired(today))

    if mode == "new":
        if target is None:
            raise PricingError("plan_not_found", "Plan not found", 404)
        tier = target
        # The app floors the stepper at the live limit when there is a current plan (also an expired one), else 10.
        min_staff = max(MIN_STAFF, current.staff_limit) if current else MIN_STAFF
        term_months = int(tier.months)
        free_months = free_months_for_term(tier, term_months)
        months = term_months - free_months                    # months charged; the term (validity) is the full duration
        credit = ZERO
        if current and not expired:
            # what the unexpired plan cost: its PAID months, not the free ones
            credit = money(CREDIT_BASELINE_STAFF * current.plan.price * current.plan.paid_months)
        charged_staff = staff
        valid_from = today
        valid_till = add_months(today, term_months)           # validity is the full duration, free months included
        resulting = staff
    elif mode == "renew":
        if current is None:
            raise PricingError("no_current_plan", "There is no plan to renew")
        tier = current.plan
        min_staff = MIN_STAFF
        term_months = RENEW_MONTHS
        free_months = free_months_for_term(tier, RENEW_MONTHS)
        months = RENEW_MONTHS - free_months
        credit = ZERO
        charged_staff = staff
        valid_from = current.end_date if not expired else today
        valid_till = add_months(valid_from, RENEW_MONTHS)
        resulting = staff
    else:  # upgrade
        if current is None:
            raise PricingError("no_current_plan", "There is no plan to add staff to")
        if expired:
            raise PricingError("plan_expired", "Your plan has expired. Please renew it first")
        tier = current.plan
        min_staff = 1
        months = calendar_months_left(today, current.end_date)
        free_months = 0                       # extra staff pay the monthly rate for every month left; free months never apply
        credit = ZERO
        charged_staff = staff
        term_months = 0
        valid_from = today
        valid_till = current.end_date
        resulting = current.staff_limit + staff

    if mode != "upgrade" and staff < min_staff:
        raise PricingError("staff_below_minimum", f"Staff count must be at least {min_staff}")
    price = to_decimal(tier.price)
    if price <= 0 or months < 1:
        raise PricingError("plan_not_priced", "This plan is not available for purchase right now")

    staff_charge = money(charged_staff * price * months)
    after_credit = max(ZERO, staff_charge - credit)

    # Refer & Earn: a coupon and a reward never share a purchase (checked before anything else is priced)
    if apply_referral and code:
        raise PricingError("referral_with_coupon", REFERRAL_MSG_WITH_COUPON, 400)
    offer = referral or ReferralOffer()
    ref_available = max(0, int(offer.available_count or 0))
    ref_percent = to_decimal(offer.percent, D(REFERRAL_PERCENT))
    ref_why: Optional[str] = None                  # why THIS quote cannot use a reward
    if mode not in REFERRAL_MODES:
        ref_why = REFERRAL_MSG_UPGRADE
    elif int(tier.plan_id) not in REFERRAL_ELIGIBLE_PLAN_IDS and tier.family_id not in REFERRAL_ELIGIBLE_PLAN_IDS:
        ref_why = REFERRAL_MSG_PLAN
    elif ref_available < 1 or offer.reward_id is None:
        ref_why = REFERRAL_MSG_NONE
    ref_eligible = ref_why is None
    ref_applied = False
    ref_message: Optional[str] = None
    ref_reward_id: Optional[int] = None
    ref_discount = ZERO
    if apply_referral:
        if ref_eligible and 0 < ref_percent <= 100:
            ref_discount = max(ZERO, min(whole_rupee(after_credit * ref_percent / 100), after_credit))
            ref_applied = True
            ref_reward_id = int(offer.reward_id)
        elif strict_referral:
            raise PricingError("referral_not_applicable", ref_why or REFERRAL_MSG_NONE, 400)
        else:
            ref_message = ref_why or REFERRAL_MSG_NONE

    discount = ZERO
    percent: Optional[Decimal] = None
    applied = False
    message: Optional[str] = None
    if ref_applied:
        discount = ref_discount
        percent = ref_percent
    elif code:
        try:
            if mode == "upgrade":
                raise CouponError("coupon_not_applicable", "Coupons do not apply to staff upgrades")
            if coupon is None:
                raise CouponError("coupon_invalid", "Invalid or expired coupon")
            discount = coupon.discount_for(after_credit, tier.plan_id, today, tier.family_id)
            applied = True
            if (coupon.discount_type or "percent") == "percent":
                percent = to_decimal(coupon.discount)
        except CouponError as exc:
            if strict_coupon and mode != "upgrade":
                raise
            message = exc.message
            discount = ZERO
    subtotal = max(ZERO, after_credit - discount)
    gst = whole_rupee(subtotal * GST_PERCENT / 100)
    total = money(subtotal + gst)

    return Quote(
        mode=mode,
        plan_id=int(tier.plan_id),
        plan_name=tier.name,
        price_per_staff_month=price,
        charged_staff=charged_staff,
        charged_months=months,
        term_months=term_months,
        staff_charge=staff_charge,
        credit=credit,
        after_credit=after_credit,
        coupon_code=code,
        coupon_applied=applied,
        coupon_message=message,
        discount_percent=percent,
        discount_amount=discount,
        subtotal=subtotal,
        gst_percent=GST_PERCENT,
        gst=gst,
        grand_total=total,
        valid_from=valid_from,
        valid_till=valid_till,
        resulting_staff_limit=resulting,
        min_staff=min_staff,
        has_current_plan=has_current,
        is_plan_expired=expired,
        referral_applied=ref_applied,
        referral_percent=ref_percent,
        referral_message=ref_message,
        referral_available_count=ref_available,
        referral_eligible=ref_eligible,
        referral_eligible_message=ref_why,
        referral_reward_id=ref_reward_id,
        free_months=free_months,
    )


def pick_mode(
    *,
    target: Optional[PlanTier],
    staff: Any,
    current: Optional[CurrentSubscription],
    today: date,
    coupon: Optional[Coupon] = None,
    coupon_code: Optional[str] = None,
    client_amount: Any = None,
    referral: Optional[ReferralOffer] = None,
    apply_referral: bool = False,
) -> str:
    """Mode for a request that did not send one (older app builds).

    new, renew and a staff-only upgrade all send the current ``plan_id``, so the intent cannot be read from the ids.
    Every candidate is priced on the server and the one whose total equals the amount the app displayed wins
    (order new, renew, upgrade); the amount only SELECTS among legitimate server prices, it is never charged.
    With no match (or no amount) the default is: no plan or a different plan -> new; the same plan -> renew when it
    has expired, upgrade while it is running.
    """
    shown = None
    if client_amount is not None:
        try:
            shown = money(client_amount)
        except PricingError:
            shown = None
    same_plan = bool(current and target and current.plan.plan_id == target.plan_id)
    candidates = []
    if target is not None:
        candidates.append("new")
    if current is not None and (target is None or same_plan):
        candidates.extend(["renew", "upgrade"])
    if shown is not None:
        for cand in candidates:
            try:
                q = build_quote(mode=cand, target=target, staff=staff, current=current, today=today,
                                coupon=coupon, coupon_code=coupon_code, strict_coupon=False,
                                referral=referral, apply_referral=apply_referral, strict_referral=False)
            except PricingError:
                continue
            if q.grand_total == shown:
                return cand
    if current is None or (target is not None and not same_plan):
        return "new"
    return "renew" if current.is_expired(today) else "upgrade"


# ── activation (what the subscription row becomes once an order is paid) ─────
def activation_terms(
    *,
    mode: str,
    term_months: int,
    ordered_staff: int,
    existing: Optional[CurrentSubscription],
    today: date,
) -> Tuple[date, date, int]:
    """(start_date, end_date, staff_limit) of the subscription created for a paid order, evaluated at payment time.

    new     replaces the limit with the ordered staff, term today + months.
    renew   extends from the current end date while it has not passed (inclusive), else from today; the limit becomes
            the ordered staff (the app's stepper, minimum 10), never "existing + ordered".
    upgrade adds the extra staff to the running limit and keeps the end date. If the term has meanwhile run out, the
            extra staff get a fresh term of the months that were paid for.
    """
    ordered_staff = int(ordered_staff)
    term_months = int(term_months or 0)
    running = existing is not None and not existing.is_expired(today)
    if mode == "renew":
        if existing is None:
            return today, add_months(today, term_months or RENEW_MONTHS), ordered_staff
        base = existing.end_date if running else today
        return today, add_months(base, term_months or RENEW_MONTHS), ordered_staff
    if mode == "upgrade":
        if running:
            return today, existing.end_date, int(existing.staff_limit) + ordered_staff
        return today, add_months(today, max(1, term_months)), ordered_staff
    return today, add_months(today, max(1, term_months)), ordered_staff   # new


# ── GST split (invoice) ─────────────────────────────────────────────────────
def _norm_state(value: Optional[str]) -> str:
    text = re.sub(r"\(\s*\d+\s*\)", " ", str(value or "").lower())     # drop a GST state code such as "(33)"
    return re.sub(r"[^a-z]+", " ", text).strip()


def is_intra_state(customer_state: Optional[str], place_of_supply: Optional[str] = None) -> bool:
    """True when CGST + SGST apply (seller and customer in the same state), False when IGST applies.

    The seller's state is the configured place of supply (``SELLER_PLACE_OF_SUPPLY``, e.g. "Tamil Nadu (33)").
    When either side is unknown the split stays CGST + SGST, as before.
    """
    seller = _norm_state(branding.seller_place_of_supply() if place_of_supply is None else place_of_supply)
    customer = _norm_state(customer_state)
    if not seller or not customer:
        return True
    return seller == customer


def gst_split(gst: Any, intra_state: bool = True) -> Dict[str, Decimal]:
    """{'cgst','sgst','igst'} adding up to ``gst`` exactly."""
    gst = money(gst)
    if not intra_state:
        return {"cgst": ZERO, "sgst": ZERO, "igst": gst}
    cgst = money(gst / 2)
    return {"cgst": cgst, "sgst": money(gst - cgst), "igst": ZERO}


def invoice_amounts(subtotal: Any, gst: Any, customer_state: Optional[str] = None) -> Dict[str, Decimal]:
    """Invoice figures from the stored order breakdown: base (taxable), cgst, sgst, igst, total."""
    base = money(subtotal)
    split = gst_split(gst, is_intra_state(customer_state))
    return {"base_amount": base, **split, "total_amount": money(base + split["cgst"] + split["sgst"] + split["igst"])}


def invoice_number(sequence_value: int, today: date) -> str:
    """``INV-<year>-<n>``: zero-padded to 4 digits, never truncated (10000 stays 10000)."""
    return f"INV-{today.year}-{int(sequence_value):04d}"


def legacy_split_from_total(total: Any) -> Dict[str, Decimal]:
    """Back-compute base / GST from a GST-inclusive total (only for old orders without a stored breakdown)."""
    total = money(total)
    base = money(total * 100 / (100 + GST_PERCENT))
    gst = money(total - base)
    return {"base": base, "gst": gst}
