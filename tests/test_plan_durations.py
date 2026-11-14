"""Plans sold in several durations with free months (migration 039): pricing maths, tier matching by name, the plan
endpoints' validation and the schema files. No database: the cursor is a script.

    6 months with 1 free  = pay for 5, valid for 6      12 months with 2 free = pay for 10, valid for 12
Run: python -m pytest tests/test_plan_durations.py -q
"""
import asyncio
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import gen_plans_functions as gp  # noqa: E402
from core_functions import pricing as P  # noqa: E402
from core_functions.pricing import Coupon, CouponError, CurrentSubscription, PlanTier, ReferralOffer, build_quote  # noqa: E402

D = Decimal
TODAY = date(2026, 11, 2)


def tier(pid, name, months, free=0, price="49"):
    return PlanTier(plan_id=pid, name=name, price=D(price), months=months, free_months=free)


CORE_1, CORE_6, CORE_12 = tier(1, "Core", 1), tier(4, "Core", 6, 1), tier(7, "Core", 12, 2)
PRIME_1, PRIME_6, PRIME_12 = tier(2, "Prime", 1, price="99"), tier(5, "Prime", 6, 1, "99"), tier(8, "Prime", 12, 2, "99")
ELITE_12 = tier(9, "Elite", 12, 2, "149")


def current(plan, end=date(2027, 3, 1), staff=10, status="ACTIVE"):
    return CurrentSubscription(plan=plan, status=status, start_date=date(2026, 3, 1), end_date=end, staff_limit=staff)


def quote(mode, target, staff, cur=None, **kw):
    return build_quote(mode=mode, target=target, staff=staff, current=cur, today=TODAY, **kw)


def run(coro):
    return asyncio.run(coro)


# ── the numbers: what the owner described ───────────────────────────────────────────────────────────────────────────
class TestFreeMonthPrices:
    def test_the_three_durations_of_core(self):
        # per staff: 1 month 49, 6 months 245 (5 x 49), 12 months 490 (10 x 49)
        assert quote("new", CORE_1, 10).staff_charge == D("490.00")
        assert quote("new", CORE_6, 10).staff_charge == D("2450.00")
        assert quote("new", CORE_12, 10).staff_charge == D("4900.00")

    def test_validity_is_the_whole_term_not_the_paid_months(self):
        for plan, months in ((CORE_1, 1), (CORE_6, 6), (CORE_12, 12)):
            q = quote("new", plan, 10)
            assert q.term_months == months and q.valid_till == P.add_months(TODAY, months) and q.valid_from == TODAY

    def test_the_quote_reports_paid_and_free_months(self):
        q = quote("new", CORE_6, 10)
        assert (q.charged_months, q.free_months, q.term_months) == (5, 1, 6)
        d = q.to_dict()
        assert (d["charged_months"], d["free_months"], d["term_months"], d["price_per_staff_month"]) == (5, 1, 6, 49.0)

    def test_gst_and_coupon_apply_to_the_discounted_charge(self):
        q = quote("new", CORE_12, 10, coupon=Coupon(name="TEN", discount=D("10")))
        assert (q.staff_charge, q.discount_amount, q.subtotal, q.gst, q.grand_total) == (
            D("4900.00"), D("490.00"), D("4410.00"), D("794.00"), D("5204.00"))

    def test_a_plan_without_free_months_is_exactly_as_before(self):
        plain = PlanTier(plan_id=2, name="Prime", price=D("40"), months=12)             # free_months defaults to 0
        q = quote("new", plain, 15)
        assert (q.staff_charge, q.grand_total, q.charged_months, q.free_months) == (D("7200.00"), D("8496.00"), 12, 0)


class TestPaidMonthsHelpers:
    @pytest.mark.parametrize("months,free,paid", [
        (1, 0, 1), (6, 1, 5), (12, 2, 10),
        (6, 0, 6), (6, None, 6),
        (6, 6, 1), (6, 99, 1), (1, 1, 1),         # a bad row can never price a purchase at zero: at least one month is charged
        (6, -3, 6),                                # nor below the duration
    ])
    def test_paid_months(self, months, free, paid):
        assert P.paid_months(months, free) == paid

    @pytest.mark.parametrize("plan,term,free", [
        (CORE_6, 6, 1), (CORE_12, 12, 2), (CORE_1, 1, 0),
        (CORE_6, 12, 2),                           # a 12 month renewal of a 6 month / 1 free plan: the same share, 2 free
        (CORE_12, 12, 2), (CORE_1, 12, 0),
        (tier(9, "Core", 4, 1), 12, 3),            # 1 free of 4 months -> 3 free of 12
        (tier(9, "Core", 7, 1), 12, 1),            # rounded down: 12 / 7 = 1.7 -> 1
    ])
    def test_free_months_for_term(self, plan, term, free):
        assert P.free_months_for_term(plan, term) == free

    def test_the_tier_properties(self):
        assert CORE_6.paid_months == 5 and CORE_12.paid_months == 10 and CORE_1.paid_months == 1


# ── upgrades: nothing that was true before changes ──────────────────────────────────────────────────────────────────
class TestUpgradesAreNotAffected:
    def test_adding_staff_costs_the_same_with_or_without_free_months(self):
        end = date(2027, 3, 1)                                                          # 4 whole calendar months left
        with_free = quote("upgrade", None, 8, current(tier(8, "Prime", 12, 2, "99"), end, 12))
        without = quote("upgrade", None, 8, current(tier(2, "Prime", 12, 0, "99"), end, 12))
        assert with_free.staff_charge == without.staff_charge == D("3168.00")           # 8 x 99 x 4
        assert with_free.grand_total == without.grand_total
        assert (with_free.charged_months, with_free.free_months, with_free.term_months) == (4, 0, 0)
        assert with_free.valid_till == end and with_free.resulting_staff_limit == 20
        assert with_free.min_staff == 1 and with_free.credit == D("0.00")

    def test_adding_staff_to_an_expired_plan_is_still_refused(self):
        with pytest.raises(P.PricingError) as exc:
            quote("upgrade", None, 8, current(CORE_12, end=date(2026, 5, 1), status="EXPIRED"))
        assert exc.value.code == "plan_expired"

    def test_moving_up_to_a_longer_plan_credits_what_the_current_plan_cost(self):
        # Core 6 months (1 free) cost 10 x 49 x 5 = 2450; Prime 12 months (2 free) = 20 x 99 x 10 = 19800
        q = quote("new", PRIME_12, 20, current(CORE_6, end=date(2027, 3, 1), staff=20))
        assert q.credit == D("2450.00") and q.staff_charge == D("19800.00") and q.after_credit == D("17350.00")
        assert (q.charged_months, q.term_months, q.free_months) == (10, 12, 2)

    def test_credit_is_the_paid_months_not_the_free_ones(self):
        no_free = quote("new", PRIME_12, 10, current(tier(4, "Core", 6, 0), end=date(2027, 3, 1)))
        free = quote("new", PRIME_12, 10, current(CORE_6, end=date(2027, 3, 1)))
        assert no_free.credit == D("2940.00") and free.credit == D("2450.00")           # 10 x 49 x 6   vs   10 x 49 x 5

    def test_an_expired_plan_gives_no_credit(self):
        q = quote("new", PRIME_12, 10, current(CORE_12, end=date(2026, 5, 1), status="EXPIRED"))
        assert q.credit == D("0.00")

    def test_the_credit_can_never_make_a_purchase_negative(self):
        q = quote("new", CORE_1, 10, current(ELITE_12, end=date(2027, 3, 1)))
        assert q.after_credit == D("0.00") and q.grand_total == D("0.00")

    def test_renewal_keeps_the_free_month_share_of_the_current_duration(self):
        assert quote("renew", None, 10, current(CORE_12)).charged_months == 10          # 12 months / 2 free
        q = quote("renew", None, 10, current(CORE_6))                                   # 6 months / 1 free
        assert (q.charged_months, q.term_months, q.free_months, q.staff_charge) == (10, 12, 2, D("4900.00"))
        q = quote("renew", None, 10, current(CORE_1))                                   # no free months: the full 12
        assert (q.charged_months, q.free_months, q.staff_charge) == (12, 0, D("5880.00"))

    def test_renewal_validity_runs_from_the_end_date_for_the_whole_year(self):
        q = quote("renew", None, 10, current(CORE_12, end=date(2027, 3, 1)))
        assert q.valid_from == date(2027, 3, 1) and q.valid_till == date(2028, 3, 1)

    def test_activation_extends_by_the_term_not_the_paid_months(self):
        # the order stores term_months = 12; the subscription is written for 12 months even though 10 were charged
        start, end, staff = P.activation_terms(mode="new", term_months=12, ordered_staff=15, existing=None, today=TODAY)
        assert (start, end, staff) == (TODAY, date(2027, 11, 2), 15)
        _, end, _ = P.activation_terms(mode="renew", term_months=12, ordered_staff=10, existing=current(CORE_12, end=date(2027, 3, 1)),
                                       today=TODAY)
        assert end == date(2028, 3, 1)


# ── every duration of a plan is the same tier ────────────────────────────────────────────────────────────────────────
class TestTierMatchingByName:
    @pytest.mark.parametrize("pid,name,family", [
        (1, "Core", 1), (4, "Core", 1), (7, "core", 1), (2, "Prime", 2), (5, " PRIME ", 2), (8, "Prime", 2),
        (3, "Elite", 3), (9, "Elite", 3), (30, "Platinum", 30), (31, None, 31),
    ])
    def test_plan_family_id(self, pid, name, family):
        assert P.plan_family_id(name, pid) == family

    def test_referral_reward_works_on_every_prime_and_elite_duration(self):
        offer = ReferralOffer(available_count=1, reward_id=5)
        for plan in (PRIME_1, PRIME_6, PRIME_12, ELITE_12):
            q = quote("new", plan, 10, referral=offer, apply_referral=True)
            assert q.referral_eligible and q.referral_applied and q.discount_percent == D("10"), plan
        for plan in (CORE_1, CORE_6, CORE_12):                                          # Core stays out
            q = quote("new", plan, 10, referral=offer, apply_referral=False)
            assert not q.referral_eligible and q.referral_eligible_message == P.REFERRAL_MSG_PLAN, plan

    def test_a_coupon_for_a_tier_covers_every_duration_of_it(self):
        coupon = Coupon(name="PRIMEONLY", discount=D("10"), plan_ids=(2,))
        for plan in (PRIME_1, PRIME_6, PRIME_12):
            assert quote("new", plan, 10, coupon=coupon).coupon_applied, plan
        with pytest.raises(CouponError) as exc:
            quote("new", CORE_12, 10, coupon=coupon)
        assert exc.value.code == "coupon_wrong_plan"

    def test_a_coupon_for_one_exact_row_still_means_only_that_row(self):
        coupon = Coupon(name="ONLY12", discount=D("10"), plan_ids=(8,))                 # Prime 12 months
        assert quote("new", PRIME_12, 10, coupon=coupon).coupon_applied
        with pytest.raises(CouponError):
            quote("new", PRIME_6, 10, coupon=coupon)


# ── the plan endpoints ───────────────────────────────────────────────────────────────────────────────────────────────
class Cur:
    def __init__(self, row=None, rows=None):
        self.row, self.rows, self.sql = row, rows or [], []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def db(monkeypatch):
    def install(row=None, rows=None):
        cur = Cur(row, rows)
        conn = Conn(cur)
        monkeypatch.setattr(gp, "get_db_connection", lambda: conn)
        monkeypatch.setattr(gp, "return_connection", lambda c: None)
        return cur, conn
    return install


class TestPlanCatalogue:
    def test_get_plans_returns_free_months_and_offer_text_shortest_duration_first(self, db):
        cur, _ = db(rows=[{"plan_id": 4, "plan_name": "Core", "plan_amount": D("49"), "plan_month": 6,
                           "plan_free_months": 1, "plan_offer_text": None}])
        out = run(gp.get_plans())
        sql = cur.sql[0][0]
        assert "free_months AS plan_free_months" in sql and "offer_text AS plan_offer_text" in sql
        assert sql.endswith("ORDER BY plan_amount ASC, plan_month ASC")
        assert out["data"][0]["plan_free_months"] == 1

    def test_list_plans_for_the_console_has_the_same_columns(self, db):
        cur, _ = db(rows=[])
        run(gp.list_plans())
        assert "free_months AS plan_free_months" in cur.sql[0][0]

    def test_subscription_reports_the_free_months_of_the_current_plan(self, db, monkeypatch):
        monkeypatch.setattr(gp, "ist_today", lambda: TODAY)
        monkeypatch.setattr(gp, "expire_due_subscriptions", lambda *a, **k: 0)
        cur, _ = db(row=None)
        run(gp.get_org_subscription("org-1"))
        assert "gp.free_months AS plan_free_months" in cur.sql[0][0]

    def test_trial_placeholder_has_the_new_key(self, db, monkeypatch):
        monkeypatch.setattr(gp, "ist_today", lambda: TODAY)
        monkeypatch.setattr(gp, "expire_due_subscriptions", lambda *a, **k: 0)
        db(row=None)
        assert run(gp.get_org_subscription("org-1"))["data"]["plan_free_months"] is None


class TestPlanWrites:
    def test_create_with_free_months_and_text(self, db):
        cur, conn = db(row={"plan_id": 4})
        out = run(gp.create_plan("Core", 49.0, 6, 1, "  Pay for 5, get 1 free  "))
        sql, params = cur.sql[0]
        assert "INSERT INTO gen_plans (plan_id, plan_name, plan_amount, plan_month, free_months, offer_text)" in sql
        assert params == ("Core", 49.0, 6, 1, "Pay for 5, get 1 free") and conn.commits == 1 and out["success"] is True

    def test_create_without_them_keeps_the_old_behaviour(self, db):
        cur, _ = db(row={"plan_id": 4})
        run(gp.create_plan("Core", 49.0, 1))
        assert cur.sql[0][1] == ("Core", 49.0, 1, 0, None)

    @pytest.mark.parametrize("month,free,text", [
        (6, 6, None), (6, 7, None), (6, -1, None), (1, 1, None),      # free months must leave at least one paid month
        (0, 0, None), (-6, 0, None),                                   # a duration of at least one month
        (6, 1, "x" * 81),                                              # the wording fits the column
    ])
    def test_create_rejects_what_cannot_be_priced(self, db, month, free, text):
        cur, conn = db(row={"plan_id": 4})
        with pytest.raises(HTTPException) as exc:
            run(gp.create_plan("Core", 49.0, month, free, text))
        assert exc.value.status_code == 400 and cur.sql == [] and conn.commits == 0

    def test_update_that_leaves_the_new_fields_out_keeps_the_stored_values(self, db):
        cur, _ = db(row={"plan_id": 4})
        run(gp.update_plan(4, "Core", 49.0, 6))                        # what an older console sends
        sql, params = cur.sql[0]
        assert "free_months=COALESCE(%s, free_months)" in sql and "CASE WHEN %s IS NULL THEN offer_text" in sql
        assert params == ("Core", 49.0, 6, None, None, None, 4)

    def test_update_can_set_and_clear_the_wording(self, db):
        cur, _ = db(row={"plan_id": 4})
        run(gp.update_plan(4, "Core", 49.0, 6, 1, "Best value"))
        assert cur.sql[0][1] == ("Core", 49.0, 6, 1, "Best value", "Best value", 4)
        run(gp.update_plan(4, "Core", 49.0, 6, 1, "   "))              # blank clears it
        assert cur.sql[1][1] == ("Core", 49.0, 6, 1, "", "", 4)

    def test_update_validates_free_months_against_the_duration(self, db):
        with pytest.raises(HTTPException) as exc:
            run(gp.update_plan(4, "Core", 49.0, 6, 6))
        assert exc.value.status_code == 400

    def test_update_of_a_missing_plan_is_404(self, db):
        db(row=None)
        with pytest.raises(HTTPException) as exc:
            run(gp.update_plan(99, "Core", 49.0, 6, 1))
        assert exc.value.status_code == 404


# ── the schema files ─────────────────────────────────────────────────────────────────────────────────────────────────
DDL = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
MIG = (ROOT / "migrations" / "039_plan_free_months.sql").read_text(encoding="utf-8")


class TestSchemaFiles:
    def test_ddl_has_the_columns_the_check_and_the_unique_duration(self):
        table = re.search(r"CREATE TABLE clockyn_dev\.gen_plans \((.*?)\n\);", DDL, re.S).group(1)
        assert "free_months int4 DEFAULT 0 NOT NULL" in table and "offer_text varchar(80) NULL" in table
        assert "CHECK (free_months >= 0 AND free_months < plan_month)" in table
        assert "gen_plans_name_month_key ON clockyn_dev.gen_plans USING btree (lower(plan_name), plan_month)" in DDL

    def test_ddl_seed_has_three_durations_of_each_plan(self):
        seed = re.search(r"INSERT INTO clockyn_dev\.gen_plans \((.*?)\)\s+VALUES\s+(.*?)\s+ON CONFLICT", DDL, re.S)
        rows = re.findall(r"\((\d+), '(\w+)',\s+(\d+),\s+([\d.]+), (\d+)\)", seed.group(2))
        assert {(n, int(m), int(f)) for _, n, m, _, f in rows} == {
            (n, m, f) for n in ("Core", "Prime", "Elite") for m, f in ((1, 0), (6, 1), (12, 2))}
        assert [int(r[0]) for r in rows] == list(range(1, 10))                          # 1/2/3 stay Core/Prime/Elite
        for name in ("Core", "Prime", "Elite"):                                         # one list price per plan
            assert len({r[3] for r in rows if r[1] == name}) == 1

    def test_the_migration_is_idempotent_and_never_inserts_prices(self):
        code = "\n".join(line for line in MIG.splitlines() if not line.lstrip().startswith("--"))
        assert "ADD COLUMN IF NOT EXISTS free_months int4 NOT NULL DEFAULT 0" in code
        assert "ADD COLUMN IF NOT EXISTS offer_text varchar(80)" in code
        assert "CREATE UNIQUE INDEX IF NOT EXISTS gen_plans_name_month_key" in code
        assert "HAVING count(*) > 1" in code                                            # duplicates are checked first
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP)\b", code, re.I)            # the owner's prices are not touched
