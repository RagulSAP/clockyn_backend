"""Pure tests of core_functions/pricing.py (no database, no network).

The five reference totals are the app's own worked examples (src/features/billing/__tests__/pricing.test.ts and
CheckoutScreen.test.tsx): 8,496 / 7,646 / 8,602 / renew 3,540 / staff-only 1,510.
Run: python -m pytest tests/test_pricing.py -q
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import pricing as P  # noqa: E402
from core_functions.pricing import (  # noqa: E402
    Coupon, CouponError, CurrentSubscription, PlanTier, PricingError, build_quote,
)

D = Decimal
TODAY = date(2026, 11, 2)


def tier(pid=2, name="Prime", price="40", months=12):
    return PlanTier(plan_id=pid, name=name, price=D(price), months=months)


def sub(plan=None, end=date(2027, 3, 1), staff=12, status="ACTIVE", start=date(2026, 3, 1)):
    return CurrentSubscription(plan=plan or tier(1, "Core", "25", 6), status=status, start_date=start, end_date=end, staff_limit=staff)


def pct(name="SAVE10", p="10", **kw):
    return Coupon(name=name, discount=D(p), **kw)


def amounts(q):
    return (q.staff_charge, q.credit, q.discount_amount, q.subtotal, q.gst, q.grand_total)


# ── the five reference prices ───────────────────────────────────────────────
class TestReferencePrices:
    def test_new_plan_8496(self):
        q = build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY)
        assert amounts(q) == (D("7200.00"), D("0.00"), D("0.00"), D("7200.00"), D("1296.00"), D("8496.00"))
        assert q.amount_paise == 849600
        assert q.valid_from == TODAY and q.valid_till == date(2027, 11, 2)
        assert q.resulting_staff_limit == 15

    def test_new_plan_with_ten_percent_coupon_7646(self):
        q = build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY, coupon=pct())
        assert amounts(q) == (D("7200.00"), D("0.00"), D("720.00"), D("6480.00"), D("1166.00"), D("7646.00"))
        assert q.discount_percent == D("10") and q.coupon_applied

    def test_plan_change_with_credit_and_coupon_8602(self):
        # 20 x 40 x 12 = 9600; credit 10 x 25 x 6 = 1500; 8100; -810; 7290; GST round(1312.2) = 1312
        q = build_quote(mode="new", target=tier(), staff=20, current=sub(), today=TODAY, coupon=pct())
        assert amounts(q) == (D("9600.00"), D("1500.00"), D("810.00"), D("7290.00"), D("1312.00"), D("8602.00"))

    def test_plan_change_with_credit_no_coupon_9558(self):
        q = build_quote(mode="new", target=tier(), staff=20, current=sub(), today=TODAY)
        assert q.grand_total == D("9558.00")      # (9600 - 1500) = 8100 + GST 1458

    def test_renew_3540(self):
        cur = sub(tier(1, "Core", "25", 6), end=date(2027, 3, 1), staff=12)
        q = build_quote(mode="renew", target=None, staff=10, current=cur, today=TODAY)
        assert amounts(q) == (D("3000.00"), D("0.00"), D("0.00"), D("3000.00"), D("540.00"), D("3540.00"))
        assert q.charged_months == 12 and q.term_months == 12
        assert q.valid_from == date(2027, 3, 1) and q.valid_till == date(2028, 3, 1)   # extends from the CURRENT end date
        assert q.resulting_staff_limit == 10

    def test_staff_only_upgrade_1510(self):
        cur = sub(tier(3, "Elite", "40", 12), end=date(2027, 3, 1), staff=12)       # 4 whole months left (Nov -> Mar)
        q = build_quote(mode="upgrade", target=None, staff=8, current=cur, today=TODAY)
        assert q.charged_months == 4
        assert amounts(q) == (D("1280.00"), D("0.00"), D("0.00"), D("1280.00"), D("230.00"), D("1510.00"))
        assert q.resulting_staff_limit == 20
        assert q.valid_till == date(2027, 3, 1) and q.term_months == 0
        assert q.plan_id == 3


# ── rounding ────────────────────────────────────────────────────────────────
class TestRounding:
    @pytest.mark.parametrize("value,expected", [
        ("1166.4", "1166.00"), ("1166.5", "1167.00"), ("0.5", "1.00"), ("2.5", "3.00"), ("1312.2", "1312.00"), ("0", "0.00"),
    ])
    def test_whole_rupee_is_half_up_not_bankers(self, value, expected):
        assert P.whole_rupee(value) == D(expected)      # Python round(2.5) would be 2

    def test_half_rupee_gst_rounds_up(self):
        # subtotal 2500 -> GST 450.00; subtotal 2501 -> 450.18 -> 450; subtotal 2503 -> 450.54 -> 451
        assert build_quote(mode="new", target=tier(price="2503"), staff=10, current=None, today=TODAY).gst != 0
        q = build_quote(mode="renew", target=None, staff=10, current=sub(tier(1, "Core", "0.25", 1)), today=TODAY)
        assert q.staff_charge == D("30.00") and q.gst == D("5.00")     # 5.4 -> 5

    def test_fractional_price_is_exact(self):
        q = build_quote(mode="new", target=tier(price="33.33"), staff=10, current=None, today=TODAY)
        assert q.staff_charge == D("3999.60")

    def test_paise_never_truncates(self):
        assert P.to_paise(D("19.99")) == 1999 and P.to_paise(19.99) == 1999 and P.to_paise("0.29") == 29
        assert P.to_paise(8602) == 860200

    def test_amounts_have_two_places(self):
        q = build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY)
        for v in amounts(q):
            assert v == v.quantize(D("0.01"))


# ── coupons ─────────────────────────────────────────────────────────────────
class TestCoupons:
    def new(self, coupon, staff=15, **kw):
        return build_quote(mode="new", target=tier(), staff=staff, current=None, today=TODAY, coupon=coupon,
                           coupon_code=coupon.name if coupon else kw.pop("code", None), **kw)

    def test_unknown_code(self):
        with pytest.raises(CouponError) as e:
            build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY, coupon=None, coupon_code="nope")
        assert e.value.message == "Invalid or expired coupon" and e.value.status == 400

    def test_unknown_code_non_strict_is_reported_not_applied(self):
        q = build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY, coupon=None, coupon_code="nope",
                        strict_coupon=False)
        assert not q.coupon_applied and q.coupon_message == "Invalid or expired coupon" and q.grand_total == D("8496.00")

    def test_inactive_status(self):
        with pytest.raises(CouponError):
            self.new(pct(status=0))

    def test_expired_the_day_after_the_last_valid_day(self):
        assert self.new(pct(valid_until=TODAY)).coupon_applied             # inclusive end date
        with pytest.raises(CouponError) as e:
            self.new(pct(valid_until=date(2026, 11, 1)))
        assert e.value.code == "coupon_expired"

    def test_not_started(self):
        with pytest.raises(CouponError) as e:
            self.new(pct(valid_from=date(2026, 11, 3)))
        assert e.value.code == "coupon_not_started"
        assert self.new(pct(valid_from=TODAY)).coupon_applied

    def test_wrong_plan(self):
        with pytest.raises(CouponError) as e:
            self.new(pct(plan_ids=(1, 3)))
        assert e.value.code == "coupon_wrong_plan"
        assert self.new(pct(plan_ids=(2,))).coupon_applied
        assert self.new(pct(plan_ids=())).coupon_applied                  # empty = every plan

    def test_min_amount_is_checked_on_the_amount_after_credit(self):
        assert self.new(pct(min_amount=D("7200"))).coupon_applied         # exactly the minimum is enough
        with pytest.raises(CouponError) as e:
            self.new(pct(min_amount=D("7200.01")))
        assert e.value.code == "coupon_min_amount"
        # credit lowers the amount: 9600 - 1500 = 8100 < 8200
        with pytest.raises(CouponError):
            build_quote(mode="new", target=tier(), staff=20, current=sub(), today=TODAY,
                        coupon=pct(min_amount=D("8200")), coupon_code="SAVE10")

    def test_max_discount_caps_a_percent_coupon(self):
        q = self.new(pct("BIG", "50", max_discount=D("1000")))
        assert q.discount_amount == D("1000.00") and q.subtotal == D("6200.00") and q.gst == D("1116.00")

    def test_flat_coupon(self):
        q = self.new(pct("FLAT", "0", discount_type="flat", flat_amount=D("500")))
        assert q.discount_amount == D("500.00") and q.subtotal == D("6700.00") and q.discount_percent is None
        assert q.grand_total == D("7906.00")                              # 6700 + 1206

    def test_flat_coupon_is_capped_by_max_discount_and_by_the_amount(self):
        assert self.new(pct("F", "0", discount_type="flat", flat_amount=D("500"), max_discount=D("200"))).discount_amount == D("200.00")
        q = self.new(pct("F", "0", discount_type="flat", flat_amount=D("99999")))
        assert q.discount_amount == D("7200.00") and q.subtotal == D("0.00") and q.grand_total == D("0.00") and not q.payable

    def test_flat_coupon_without_a_value_is_invalid(self):
        with pytest.raises(CouponError):
            self.new(pct("F", "0", discount_type="flat", flat_amount=None))

    @pytest.mark.parametrize("p", ["0", "-5", "100.01", "250"])
    def test_percent_out_of_range_is_invalid(self, p):
        with pytest.raises(CouponError):
            self.new(pct(p=p))

    def test_hundred_percent_makes_it_free_and_not_payable(self):
        q = self.new(pct("FREE", "100"))
        assert q.grand_total == D("0.00") and not q.payable

    def test_coupon_on_a_staff_upgrade_is_ignored_even_in_strict_mode(self):
        cur = sub(tier(3, "Elite", "40", 12), end=date(2027, 3, 1))
        q = build_quote(mode="upgrade", target=None, staff=8, current=cur, today=TODAY, coupon=pct(), coupon_code="SAVE10")
        assert not q.coupon_applied and q.discount_amount == D("0.00") and q.grand_total == D("1510.00")
        assert "upgrade" in q.coupon_message.lower()

    def test_code_is_normalised(self):
        q = build_quote(mode="new", target=tier(), staff=15, current=None, today=TODAY, coupon=pct("SAVE10"), coupon_code=" save10 ")
        assert q.coupon_code == "SAVE10"


# ── modes ───────────────────────────────────────────────────────────────────
class TestModes:
    def test_new_without_current_has_no_credit_and_floor_ten(self):
        with pytest.raises(PricingError) as e:
            build_quote(mode="new", target=tier(), staff=9, current=None, today=TODAY)
        assert e.value.code == "staff_below_minimum"

    def test_new_floor_is_the_live_limit_when_there_is_a_current_plan(self):
        with pytest.raises(PricingError):
            build_quote(mode="new", target=tier(), staff=11, current=sub(staff=12), today=TODAY)
        assert build_quote(mode="new", target=tier(), staff=12, current=sub(staff=12), today=TODAY).min_staff == 12

    def test_expired_current_plan_gives_no_credit(self):
        expired = sub(end=date(2026, 10, 31))
        q = build_quote(mode="new", target=tier(), staff=20, current=expired, today=TODAY)
        assert q.credit == D("0.00") and q.is_plan_expired and q.grand_total == D("11328.00")   # 9600 + 1728

    def test_status_expired_row_is_expired_even_with_a_future_date(self):
        q = build_quote(mode="new", target=tier(), staff=20, current=sub(status="EXPIRED"), today=TODAY)
        assert q.credit == D("0.00")

    def test_end_date_today_is_still_active(self):
        cur = sub(end=TODAY)
        assert not cur.is_expired(TODAY) and cur.is_expired(date(2026, 11, 3))
        q = build_quote(mode="renew", target=None, staff=10, current=cur, today=TODAY)
        assert q.valid_from == TODAY and q.valid_till == date(2027, 11, 2)

    def test_renew_expired_starts_today(self):
        cur = sub(tier(1, "Core", "25", 6), end=date(2026, 9, 1))
        q = build_quote(mode="renew", target=None, staff=10, current=cur, today=TODAY)
        assert q.valid_from == TODAY and q.valid_till == date(2027, 11, 2) and q.is_plan_expired

    def test_renew_prices_at_the_current_tier_and_ignores_target(self):
        q = build_quote(mode="renew", target=tier(3, "Elite", "99"), staff=10, current=sub(tier(1, "Core", "25", 6)), today=TODAY)
        assert q.plan_id == 1 and q.grand_total == D("3540.00")

    def test_renew_and_upgrade_need_a_plan(self):
        for mode in ("renew", "upgrade"):
            with pytest.raises(PricingError) as e:
                build_quote(mode=mode, target=tier(), staff=10, current=None, today=TODAY)
            assert e.value.code == "no_current_plan"

    def test_upgrade_on_an_expired_plan_is_refused(self):
        with pytest.raises(PricingError) as e:
            build_quote(mode="upgrade", target=None, staff=5, current=sub(end=date(2026, 10, 1)), today=TODAY)
        assert e.value.code == "plan_expired"

    @pytest.mark.parametrize("end,months", [
        (date(2026, 11, 30), 1),      # same month: at least 1
        (date(2026, 11, 2), 1),
        (date(2026, 12, 1), 1),
        (date(2027, 3, 1), 4),
        (date(2027, 11, 2), 12),
    ])
    def test_upgrade_months_left_ignore_the_day(self, end, months):
        cur = sub(tier(3, "Elite", "40", 12), end=end)
        assert build_quote(mode="upgrade", target=None, staff=1, current=cur, today=TODAY).charged_months == months

    def test_upgrade_has_no_minimum_but_at_least_one(self):
        cur = sub(tier(3, "Elite", "40", 12))
        assert build_quote(mode="upgrade", target=None, staff=1, current=cur, today=TODAY).charged_staff == 1
        with pytest.raises(PricingError):
            build_quote(mode="upgrade", target=None, staff=0, current=cur, today=TODAY)

    def test_new_requires_a_catalogue_plan(self):
        with pytest.raises(PricingError) as e:
            build_quote(mode="new", target=None, staff=10, current=None, today=TODAY)
        assert e.value.status == 404

    def test_unknown_mode(self):
        with pytest.raises(PricingError) as e:
            build_quote(mode="gift", target=tier(), staff=10, current=None, today=TODAY)
        assert e.value.code == "invalid_mode"


# ── guards ──────────────────────────────────────────────────────────────────
class TestGuards:
    @pytest.mark.parametrize("staff", [0, -1, -100, None, "abc", True, 10.5, 10001])
    def test_bad_staff_counts(self, staff):
        with pytest.raises(PricingError) as e:
            build_quote(mode="new", target=tier(), staff=staff, current=None, today=TODAY)
        assert e.value.code == "invalid_staff"

    def test_numeric_string_staff_is_accepted(self):
        assert build_quote(mode="new", target=tier(), staff="15", current=None, today=TODAY).charged_staff == 15

    @pytest.mark.parametrize("price,months", [("0", 12), ("-5", 12), ("40", 0)])
    def test_unpriced_plan_is_refused(self, price, months):
        with pytest.raises(PricingError) as e:
            build_quote(mode="new", target=tier(price=price, months=months), staff=10, current=None, today=TODAY)
        assert e.value.code == "plan_not_priced"

    def test_negative_staff_never_reduces_a_total(self):
        with pytest.raises(PricingError):
            build_quote(mode="upgrade", target=None, staff=-5, current=sub(tier(3, "Elite", "40", 12)), today=TODAY)

    def test_credit_larger_than_the_charge_floors_at_zero(self):
        big = sub(tier(1, "Core", "1000", 12), staff=10)
        q = build_quote(mode="new", target=tier(price="1"), staff=10, current=big, today=TODAY)
        assert q.after_credit == D("0.00") and q.grand_total == D("0.00") and not q.payable

    def test_to_decimal_junk(self):
        with pytest.raises(PricingError):
            P.to_decimal("abc")
        assert P.to_decimal(None, D("0")) == D("0")


# ── GST split, invoice numbers, activation ──────────────────────────────────
class TestGstAndInvoice:
    def test_intra_state_split_adds_up(self):
        assert P.gst_split(D("1296"), True) == {"cgst": D("648.00"), "sgst": D("648.00"), "igst": D("0.00")}
        s = P.gst_split(D("1167"), True)
        assert s["cgst"] + s["sgst"] == D("1167.00")

    def test_inter_state_is_igst(self):
        assert P.gst_split(D("1296"), False) == {"cgst": D("0.00"), "sgst": D("0.00"), "igst": D("1296.00")}

    @pytest.mark.parametrize("seller,customer,intra", [
        ("Tamil Nadu (33)", "Tamil Nadu", True),
        ("Tamil Nadu (33)", "tamil nadu ", True),
        ("Tamil Nadu (33)", "Karnataka", False),
        ("Tamil Nadu", "Kerala", False),
        ("", "Kerala", True),                 # unknown seller state: stays CGST + SGST (today's behaviour)
        ("Tamil Nadu (33)", None, True),      # unknown customer state
        ("Tamil Nadu (33)", "", True),
    ])
    def test_place_of_supply(self, monkeypatch, seller, customer, intra):
        monkeypatch.setenv("SELLER_PLACE_OF_SUPPLY", seller)
        assert P.is_intra_state(customer) is intra

    def test_invoice_amounts_foot_to_the_order_total(self, monkeypatch):
        monkeypatch.setenv("SELLER_PLACE_OF_SUPPLY", "Tamil Nadu (33)")
        q = build_quote(mode="new", target=tier(), staff=20, current=sub(), today=TODAY, coupon=pct())
        a = P.invoice_amounts(q.subtotal, q.gst, "Tamil Nadu")
        assert a["base_amount"] == D("7290.00") and a["cgst"] == a["sgst"] == D("656.00") and a["total_amount"] == q.grand_total
        b = P.invoice_amounts(q.subtotal, q.gst, "Kerala")
        assert b["igst"] == D("1312.00") and b["cgst"] == b["sgst"] == D("0.00") and b["total_amount"] == q.grand_total

    def test_invoice_number_is_padded_and_never_truncated(self):
        assert P.invoice_number(7, date(2026, 11, 2)) == "INV-2026-0007"
        assert P.invoice_number(9999, date(2026, 11, 2)) == "INV-2026-9999"
        assert P.invoice_number(10000, date(2026, 11, 2)) == "INV-2026-10000"

    def test_legacy_split_from_total(self):
        assert P.legacy_split_from_total("8602") == {"base": D("7289.83"), "gst": D("1312.17")}


class TestActivationTerms:
    def test_new_replaces_staff_and_starts_today(self):
        assert P.activation_terms(mode="new", term_months=12, ordered_staff=15, existing=sub(), today=TODAY) == (
            TODAY, date(2027, 11, 2), 15)

    def test_renew_active_extends_from_the_end_date_and_does_not_add_staff(self):
        s, e, n = P.activation_terms(mode="renew", term_months=12, ordered_staff=10,
                                     existing=sub(end=date(2027, 3, 1), staff=25), today=TODAY)
        assert (s, e, n) == (TODAY, date(2028, 3, 1), 10)

    def test_renew_on_the_last_valid_day_still_counts_as_active(self):
        _, e, _ = P.activation_terms(mode="renew", term_months=12, ordered_staff=10, existing=sub(end=TODAY), today=TODAY)
        assert e == date(2027, 11, 2)

    def test_renew_expired_starts_from_today(self):
        _, e, _ = P.activation_terms(mode="renew", term_months=12, ordered_staff=10,
                                     existing=sub(end=date(2026, 5, 1)), today=TODAY)
        assert e == date(2027, 11, 2)

    def test_renew_expired_status_with_future_date_starts_today(self):
        _, e, _ = P.activation_terms(mode="renew", term_months=12, ordered_staff=10,
                                     existing=sub(end=date(2027, 3, 1), status="EXPIRED"), today=TODAY)
        assert e == date(2027, 11, 2)

    def test_upgrade_adds_staff_and_keeps_the_end_date(self):
        assert P.activation_terms(mode="upgrade", term_months=0, ordered_staff=8,
                                  existing=sub(end=date(2027, 3, 1), staff=12), today=TODAY) == (TODAY, date(2027, 3, 1), 20)

    def test_upgrade_after_the_term_ran_out_gets_a_fresh_term_of_the_paid_months(self):
        s, e, n = P.activation_terms(mode="upgrade", term_months=4, ordered_staff=8,
                                     existing=sub(end=date(2026, 10, 1)), today=TODAY)
        assert (e, n) == (date(2027, 3, 2), 8)

    def test_renew_without_any_subscription_is_a_fresh_year(self):
        assert P.activation_terms(mode="renew", term_months=12, ordered_staff=10, existing=None, today=TODAY)[1] == date(2027, 11, 2)

    def test_month_end_is_clamped(self):
        assert P.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert P.add_months(date(2027, 12, 31), 2) == date(2028, 2, 29)


# ── mode inference for older app builds ─────────────────────────────────────
class TestPickMode:
    cur_active = None

    def pick(self, **kw):
        base = dict(target=tier(1, "Core", "25", 6), staff=10, current=sub(tier(1, "Core", "25", 6), end=date(2027, 3, 1), staff=12),
                    today=TODAY)
        base.update(kw)
        return P.pick_mode(**base)

    def test_amount_selects_renew(self):
        assert self.pick(client_amount=3540) == "renew"

    def test_amount_selects_upgrade(self):
        cur = sub(tier(3, "Elite", "40", 12), end=date(2027, 3, 1), staff=12)
        assert P.pick_mode(target=tier(3, "Elite", "40", 12), staff=8, current=cur, today=TODAY, client_amount=1510) == "upgrade"

    def test_amount_selects_new_for_a_different_plan(self):
        assert P.pick_mode(target=tier(), staff=20, current=sub(), today=TODAY, coupon=pct(), coupon_code="SAVE10",
                           client_amount=8602) == "new"

    def test_tampered_amount_falls_back_to_the_default_not_to_the_amount(self):
        assert self.pick(client_amount=1) == "upgrade"                       # same plan, still running
        assert self.pick(client_amount=1, current=sub(tier(1, "Core", "25", 6), end=date(2026, 5, 1))) == "renew"
        assert P.pick_mode(target=tier(), staff=15, current=None, today=TODAY, client_amount=1) == "new"
        assert P.pick_mode(target=tier(), staff=15, current=sub(), today=TODAY, client_amount=1) == "new"

    def test_no_amount_uses_the_default(self):
        assert P.pick_mode(target=None, staff=5, current=sub(), today=TODAY) == "upgrade"
