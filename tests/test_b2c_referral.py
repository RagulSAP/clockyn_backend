"""DB-free tests of the B2C referral (promoter codes) and of the C2C percent kept in the database (migration 040).

Two kinds of fake: the stateful referral fake of tests/test_referral.py (registration, checkout, payment, operator views, routes) and
a scripted cursor (the promoter CRUD, the detail insights and the payouts, which only need canned rows).
Run: python -m pytest tests/test_b2c_referral.py -q
"""
import asyncio
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import b2c_referral_functions as b2c  # noqa: E402
from core_functions import operator_auth  # noqa: E402
from core_functions import referral_functions as ref_fn  # noqa: E402
from test_orders_billing import ADMIN, ORG  # noqa: E402
from test_referral import (  # noqa: E402,F401  (fixtures and helpers of the referral tests)
    RefCursor, THIRD_ORG, REF_ORG, create_body, env, pay, quote_body, reg, register, reward, seed_referral, verify,
)

D = Decimal


def partner_row(code="CHANL5", pid=1, **over):
    row = {"partner_id": pid, "code": code, "channel_name": "Tech Talk Hindi", "platform": "youtube", "discount_percent": 15,
           "reward_valid_days": 30, "status": 1, "commission_type": "percent", "commission_value": D("10.00")}
    row.update(over)
    return row


def add_partner(env, **over):
    row = partner_row(**over)
    env.db.partners.append(row)
    return row


# ═════════════════════════════════════════════════════════════════════════ C2C percent from the database
class TestC2cPercent:
    def test_default_is_ten_and_the_row_wins(self, env):
        assert ref_fn.get_c2c_percent(RefCursor(env.db)) == 10
        env.db.c2c_percent = 12
        assert ref_fn.get_c2c_percent(RefCursor(env.db)) == 12

    def test_a_missing_or_broken_row_falls_back_to_ten(self):
        class Cur:
            def __init__(self, row): self.row = row
            def execute(self, sql, params=None): pass
            def fetchone(self): return self.row
        assert ref_fn.get_c2c_percent(Cur(None)) == 10
        assert ref_fn.get_c2c_percent(Cur({"c2c_percent": None})) == 10
        assert ref_fn.get_c2c_percent(Cur({"c2c_percent": 0})) == 10
        assert ref_fn.get_c2c_percent(Cur({"c2c_percent": 250})) == 10

    @pytest.mark.parametrize("value", [0, -1, 51, 100, 5.5, "abc", None, True, [], "12.5", float("nan"), float("inf"), "nan", "inf"])
    def test_the_console_refuses_a_bad_percent(self, value):
        with pytest.raises(HTTPException) as e:
            ref_fn.validate_percent(value)
        assert e.value.status_code == 400 and "between 1 and 50" in e.value.detail

    @pytest.mark.parametrize("value,expected", [(1, 1), (10, 10), (50, 50), ("12", 12), (12.0, 12)])
    def test_the_console_accepts_a_whole_percent(self, value, expected):
        assert ref_fn.validate_percent(value) == expected

    def test_new_referrals_get_the_current_percent_and_old_rewards_keep_theirs(self, reg):
        first_code = ref_fn.get_or_create_code(RefCursor(reg.db), REF_ORG)
        register(reg, referral_code=first_code)
        assert {r["percent"] for r in reg.db.rewards} == {10}
        reg.db.c2c_percent = 12                                                  # the operator changes it later
        out = register(reg, referral_code=first_code, mobile_number="9222222222", email="two@example.com", org_name="Second Org")
        percents = [(r["org_id"], r["percent"]) for r in reg.db.rewards]
        assert [p for _, p in percents[:2]] == [10, 10] and [p for _, p in percents[2:]] == [12, 12]
        assert out["success"] is True

    def test_the_share_message_and_the_page_use_the_percent(self, env):
        env.db.c2c_percent = 15
        page = env.client.get(f"/referral/{ORG}").json()
        assert page["percent"] == 15 and "15% off" in page["share_message"]
        assert ref_fn.share_message("K7M2QX").count("10%") == 1                      # the default is still 10

    def test_a_reward_priced_at_checkout_uses_its_own_percent(self, env):
        env.db.c2c_percent = 25                                                  # changing the setting now must not touch the reward
        seed_referral(env.db)
        env.db.c2c_percent = 12
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral_percent"] == 12 or q["referral_percent"] == 25
        assert reward(env.db, ORG)["percent"] == 25 and q["discount_amount"] == 1800.0   # 25 % of 7200


class TestConfigEndpoints:
    def op(self, env):
        import os
        os.environ["OPERATOR_ENFORCE"] = "1"
        operator_auth.set_lookup(lambda phone: {"1": 1, "2": 2}.get(phone))

    def test_read_is_for_any_operator_and_has_the_range(self, env):
        self.op(env)
        assert env.client.get("/gen-dashboard/referrals/config").status_code == 403
        body = env.client.get("/gen-dashboard/referrals/config", headers={"x-admin-id": "2"}).json()
        assert (body["c2c_percent"], body["min_percent"], body["max_percent"]) == (10, 1, 50)

    def test_only_a_super_admin_can_change_it(self, env):
        self.op(env)
        assert env.client.put("/gen-dashboard/referrals/config", json={"c2c_percent": 20}).status_code == 403
        assert env.client.put("/gen-dashboard/referrals/config", json={"c2c_percent": 20}, headers={"x-admin-id": "2"}).status_code == 403
        assert env.db.c2c_percent == 10
        r = env.client.put("/gen-dashboard/referrals/config", json={"c2c_percent": 20}, headers={"x-admin-id": "1"})
        assert r.status_code == 200 and r.json()["c2c_percent"] == 20 and env.db.c2c_percent == 20
        assert r.json()["updated_by"] is not None

    @pytest.mark.parametrize("value", [0, 51, 2.5, "x", None])
    def test_a_bad_value_is_a_400_and_changes_nothing(self, env, value):
        self.op(env)
        r = env.client.put("/gen-dashboard/referrals/config", json={"c2c_percent": value}, headers={"x-admin-id": "1"})
        assert r.status_code == 400 and env.db.c2c_percent == 10

    def test_the_c2c_overview_reports_the_percent(self, env):
        env.db.c2c_percent = 14
        assert env.client.get("/gen-dashboard/referrals").json()["percent"] == 14


# ═════════════════════════════════════════════════════════════════════════ registration with a promoter's code
class TestRegistrationWithAPartnerCode:
    def test_the_new_organisation_gets_one_reward_with_the_partners_percent_and_expiry(self, reg):
        add_partner(reg)
        out = register(reg, referral_code="chanl5")                              # case does not matter
        r = reg.db.referrals[0]
        assert (r["partner_id"], r["referrer_org_id"], r["referred_org_id"], r["code"]) == (1, None, out["org_id"], "CHANL5")
        rewards = [x for x in reg.db.rewards if x["referral_id"] == r["referral_id"]]
        assert len(rewards) == 1                                                 # the promoter gets no reward
        w = rewards[0]
        assert (w["role"], w["status"], w["percent"], w["org_id"]) == ("referred", "AVAILABLE", 15, out["org_id"])
        assert w["expires_at"] - w["available_at"] == timedelta(days=30)

    def test_a_partner_without_validity_days_never_expires(self, reg):
        add_partner(reg, reward_valid_days=None)
        register(reg, referral_code="CHANL5")
        assert reg.db.rewards[0]["expires_at"] is None

    def test_a_custom_code_may_use_the_letters_and_digits_c2c_codes_never_have(self, reg):
        add_partner(reg, code="A0B1OL")
        register(reg, referral_code="a0b1ol")
        assert reg.db.referrals[0]["code"] == "A0B1OL"

    def test_an_inactive_code_is_refused_before_anything_is_created(self, reg):
        add_partner(reg, status=0)
        with pytest.raises(HTTPException) as e:
            register(reg, referral_code="CHANL5")
        assert e.value.status_code == 400 and e.value.detail == "This referral code is not valid"
        assert reg.db.referrals == [] and "org_id" not in reg.created

    def test_a_customers_c2c_code_is_still_found_first(self, reg):
        code = ref_fn.get_or_create_code(RefCursor(reg.db), REF_ORG)
        add_partner(reg, code="OTHER9")
        register(reg, referral_code=code)
        assert reg.db.referrals[0].get("partner_id") is None and reg.db.referrals[0]["referrer_org_id"] == REF_ORG
        assert len(reg.db.rewards) == 2                                          # referred + referrer, as before

    def test_the_c2c_generator_never_hands_out_a_partner_code(self, env, monkeypatch):
        add_partner(env, code="AAAA22")
        candidates = iter(["AAAA22", "BBBB33"])
        monkeypatch.setattr(ref_fn, "generate_code", lambda rng=None: next(candidates))
        assert ref_fn.get_or_create_code(RefCursor(env.db), ORG) == "BBBB33"

    def test_an_organisation_can_use_only_one_code(self, reg):
        add_partner(reg)
        out = register(reg, referral_code="CHANL5")
        cur = RefCursor(reg.db)
        with pytest.raises(HTTPException) as e:
            ref_fn.attach_referral_at_registration(cur, out["org_id"], "CHANL5")
        assert e.value.status_code == 400 and "already used a referral code" in e.value.detail


class TestValidateEndpoint:
    def test_a_promoter_code_shows_the_channel_name_and_the_codes_percent(self, env):
        add_partner(env)
        r = env.client.get("/referral/validate", params={"code": "chanl5"})
        assert r.status_code == 200
        assert r.json() == {"valid": True, "referrer_org_name": "Tech Talk Hindi", "kind": "b2c", "percent": 15}

    def test_an_inactive_or_unknown_code_is_invalid(self, env):
        add_partner(env, status=0)
        for code in ("CHANL5", "ZZZZ99", "ab", ""):
            r = env.client.get("/referral/validate", params={"code": code})
            assert r.json() == {"valid": False, "message": "This referral code is not valid"}

    def test_a_c2c_code_reports_the_current_percent(self, env):
        env.db.c2c_percent = 12
        code = ref_fn.get_or_create_code(RefCursor(env.db), REF_ORG)
        assert env.client.get("/referral/validate", params={"code": code}).json() == {
            "valid": True, "referrer_org_name": "Zed Corp", "kind": "c2c", "percent": 12}

    def test_a_malformed_code_never_reaches_the_database(self, env):
        before = len(env.db.log)
        assert env.client.get("/referral/validate", params={"code": "ABC-12"}).json()["valid"] is False
        assert len(env.db.log) == before


# ═════════════════════════════════════════════════════════════════════════ the reward at checkout, expiry, commission
class TestCheckoutAndCommission:
    def signed_up(self, env, **over):
        add_partner(env, **over)
        cur = RefCursor(env.db)
        return ref_fn.attach_referral_at_registration(cur, ORG, "CHANL5")

    def test_the_reward_discounts_prime_by_the_codes_percent(self, env):
        self.signed_up(env)
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral_applied"] is True and q["referral_percent"] == 15
        assert q["discount_amount"] == 1080.0 and q["subtotal"] == 6120.0 and q["grand_total"] == 7222.0   # 15 x 40 x 12 = 7200

    def test_core_is_not_eligible_and_a_coupon_cannot_be_combined(self, env):
        self.signed_up(env)
        q = env.client.post("/orders/quote", json=quote_body(plan_id=1, apply_referral=True)).json()["data"]
        assert q["referral_applied"] is False and q["referral"]["eligible"] is False

    def test_an_expired_reward_is_not_offered_and_cannot_be_used(self, env):
        self.signed_up(env)
        w = reward(env.db, ORG)
        w["expires_at"] = datetime(2026, 11, 1, 0, 0, 0)                          # the order date of the tests is 2026-11-02
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral"]["available_count"] == 0 and q["referral_applied"] is False
        assert q["grand_total"] == 8496.0                                        # full price, nothing discounted
        w["expires_at"] = datetime(2026, 11, 3, 0, 0, 0)                          # a day left: usable again
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral_applied"] is True

    def test_an_order_priced_while_the_reward_was_valid_is_honoured_and_the_reward_is_used(self, env):
        self.signed_up(env)
        env.client.post("/orders/create", json=create_body(apply_referral=True, amount=7222))     # priced while valid
        reward(env.db, ORG)["expires_at"] = datetime(2026, 11, 1, 0, 0, 0)                          # expires before the payment arrives
        assert pay(env).status_code == 200
        w = reward(env.db, ORG)
        assert w["status"] == "USED" and w["used_discount_amount"] == D("1080.00")                  # reconciled, not flagged
        assert env.db.orders[0].get("payment_flag") is None
        assert env.db.invoices[0]["referral_reward_id"] == w["reward_id"]

    def test_the_commission_is_booked_once_on_the_first_payment(self, env):
        rid = self.signed_up(env)
        env.client.post("/orders/create", json=create_body(apply_referral=True, amount=7222))
        assert pay(env).status_code == 200
        r = next(x for x in env.db.referrals if x["referral_id"] == rid)
        assert r["qualified_at"] is not None and r["qualified_order_id"] == 1
        assert r["commission_amount"] == D("612.00")                              # 10 % of 6120 (the amount excluding GST)
        assert reward(env.db, ORG)["status"] == "USED"
        assert [x for x in env.db.rewards if x["org_id"] != ORG] == []            # no referrer reward appeared

    def test_a_fixed_commission_and_no_commission(self, env):
        rid = self.signed_up(env, commission_type="fixed", commission_value=D("500.00"))
        env.client.post("/orders/create", json=create_body(apply_referral=True, amount=7222))
        pay(env)
        assert next(x for x in env.db.referrals if x["referral_id"] == rid)["commission_amount"] == D("500.00")

    def test_a_partner_with_no_commission_books_zero(self, env):
        rid = self.signed_up(env, commission_type="none", commission_value=D("0"))
        env.client.post("/orders/create", json=create_body(apply_referral=True, amount=7222))
        pay(env)
        assert next(x for x in env.db.referrals if x["referral_id"] == rid)["commission_amount"] == D("0.00")

    def test_c2c_payment_still_unlocks_the_referrer_and_books_no_commission(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        pay(env)
        assert reward(env.db, REF_ORG, role="referrer")["status"] == "AVAILABLE"
        assert env.db.referrals[0].get("commission_amount") is None

    @pytest.mark.parametrize("kind,value,base,expected", [
        ("percent", "10", "6120.00", "612.00"), ("percent", "12.5", "1999.00", "249.88"), ("percent", "10", None, "0.00"),
        ("percent", "10", "0", "0.00"), ("fixed", "500", None, "500.00"), ("fixed", "0", "100", "0.00"),
        ("none", "99", "6120", "0.00"), (None, "99", "6120", "0.00"), ("percent", None, "6120", "0.00"),
    ])
    def test_commission_for(self, kind, value, base, expected):
        assert ref_fn.commission_for(kind, value, base) == D(expected)


class TestMyRewardOnThePage:
    def test_a_promoter_reward_shows_the_channel_name_kind_and_expiry(self, env):
        add_partner(env)
        ref_fn.attach_referral_at_registration(RefCursor(env.db), ORG, "CHANL5")
        page = env.client.get(f"/referral/{ORG}").json()
        mine = page["my_reward"]
        assert (mine["kind"], mine["status"], mine["percent"], mine["referred_by_org_name"]) == ("b2c", "available", 15, "Tech Talk Hindi")
        assert mine["expires_at"] and mine["expires_at"].endswith("Z")
        assert page["summary"]["rewards_available"] == 1

    def test_a_reward_past_its_expiry_reads_as_expired_and_is_not_counted_available(self, env):
        add_partner(env)
        ref_fn.attach_referral_at_registration(RefCursor(env.db), ORG, "CHANL5")
        reward(env.db, ORG)["expires_at"] = datetime(2020, 1, 1)
        page = env.client.get(f"/referral/{ORG}").json()
        assert page["my_reward"]["status"] == "expired" and page["summary"]["rewards_available"] == 0

    def test_a_c2c_reward_is_labelled_c2c_and_never_expires(self, env):
        seed_referral(env.db)
        mine = env.client.get(f"/referral/{ORG}").json()["my_reward"]
        assert (mine["kind"], mine["expires_at"], mine["status"]) == ("c2c", None, "available")


class TestC2cNumbersExcludeB2c:
    def test_the_c2c_tab_counts_only_customer_referrals(self, env):
        seed_referral(env.db, REF_ORG, ORG)
        add_partner(env)
        ref_fn.attach_referral_at_registration(RefCursor(env.db), THIRD_ORG, "CHANL5")
        body = env.client.get("/gen-dashboard/referrals").json()
        assert body["summary"]["total_referrals"] == 1 and body["summary"]["rewards_issued"] == 2
        assert [r["referred_org_id"] for r in body["referrals"]] == [ORG]
        assert [t["org_id"] for t in body["top_referrers"]] == [REF_ORG]


# ═════════════════════════════════════════════════════════════════════════ the promoter CRUD (scripted cursor)
class Script:
    """Answers by SQL fragment (first match wins); every statement is recorded in ``log``."""

    def __init__(self, answers):
        self.answers, self.log, self._rows = answers, [], []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.log.append((s, params))
        for frag, value in self.answers:
            if frag in s:
                value = value(params) if callable(value) else value
                self._rows = [] if value is None else (value if isinstance(value, list) else [value])
                return
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def find(self, frag):
        return [(s, p) for s, p in self.log if frag in s]

    def close(self):
        pass


def db_partner(**over):
    row = {"partner_id": 5, "code": "CHANL5", "platform": "youtube", "channel_name": "Tech Talk Hindi", "channel_url": None,
           "contact_name": None, "phone": None, "email": None, "address": None, "notes": None, "discount_percent": 15,
           "reward_valid_days": 30, "commission_type": "percent", "commission_value": D("10.00"), "status": 1, "created_by": "root",
           "created_at": datetime(2026, 11, 1, 9, 0), "signups": 4, "converted": 2, "commission_earned": D("700.00"),
           "revenue": D("12000.00"), "commission_paid": D("200.00")}
    row.update(over)
    return row


BODY = {"code": "chanl5", "platform": "YouTube", "channel_name": " Tech Talk Hindi ", "channel_url": "https://youtube.com/@tt",
        "contact_name": "Ravi", "phone": "+91 98765 43210", "email": "ravi@example.com", "address": "Chennai", "notes": "n",
        "discount_percent": 15, "reward_valid_days": 30, "commission_type": "percent", "commission_value": "10"}


class TestPartnerValidation:
    def test_a_full_body_is_cleaned(self):
        out = b2c.clean_partner(BODY)
        assert out["platform"] == "youtube" and out["channel_name"] == "Tech Talk Hindi"
        assert (out["discount_percent"], out["reward_valid_days"], out["commission_type"], out["commission_value"]) == (15, 30, "percent", D("10.00"))

    def test_optional_fields_may_be_left_out(self):
        out = b2c.clean_partner({"platform": "other", "channel_name": "X", "discount_percent": 5})
        assert out["reward_valid_days"] is None and out["commission_type"] == "none" and out["commission_value"] == D("0.00")
        assert out["phone"] is None and out["email"] is None

    @pytest.mark.parametrize("field,value,message", [
        ("platform", "myspace", "Platform must be one of"), ("platform", None, "Platform must be one of"),
        ("channel_name", "  ", "Channel name is required"), ("channel_name", "x" * 151, "at most 150"),
        ("channel_url", "youtube.com/x", "http://"), ("phone", "12", "7 to 15 digits"), ("phone", "98765abc10", "only digits"),
        ("email", "not-an-email", "Email is not valid"), ("address", "x" * 1001, "at most 1000"),
        ("discount_percent", 0, "between 1 and 50"), ("discount_percent", 51, "between 1 and 50"), ("discount_percent", "x", "whole number"),
        ("reward_valid_days", 0, "Reward valid days"), ("reward_valid_days", 3651, "Reward valid days"), ("reward_valid_days", 1.5, "whole number"),
        ("reward_valid_days", float("nan"), "whole number"), ("reward_valid_days", float("inf"), "whole number"),
        ("commission_value", "NaN", "must be a number"), ("commission_value", "Infinity", "must be a number"),
        ("commission_type", "bonus", "none, percent or fixed"), ("commission_value", "abc", "must be a number"),
        ("commission_value", "-1", "cannot be negative"),
    ])
    def test_a_bad_field_is_a_400_that_names_it(self, field, value, message):
        with pytest.raises(HTTPException) as e:
            b2c.clean_partner({**BODY, field: value})
        assert e.value.status_code == 400 and message in e.value.detail

    def test_nan_is_a_400_even_when_there_is_no_commission(self):
        with pytest.raises(HTTPException) as e:
            b2c.clean_partner({**BODY, "commission_type": "none", "commission_value": "NaN"})
        assert e.value.status_code == 400

    def test_commission_limits_depend_on_the_type(self):
        with pytest.raises(HTTPException):
            b2c.clean_partner({**BODY, "commission_type": "percent", "commission_value": "100.01"})
        assert b2c.clean_partner({**BODY, "commission_type": "percent", "commission_value": "100"})["commission_value"] == D("100.00")
        with pytest.raises(HTTPException):
            b2c.clean_partner({**BODY, "commission_type": "fixed", "commission_value": "1000000.01"})
        assert b2c.clean_partner({**BODY, "commission_type": "none", "commission_value": "55"})["commission_value"] == D("0.00")

    @pytest.mark.parametrize("code", ["abc", "ABCDEFG", "AB-DEF", "AB DEF", "", None, "ÄBCDEF"])
    def test_a_code_must_be_exactly_six_letters_or_digits(self, code):
        with pytest.raises(HTTPException) as e:
            b2c.normalise_partner_code(code)
        assert e.value.status_code == 400 and "6 letters or digits" in e.value.detail

    @pytest.mark.parametrize("code,expected", [("chanl5", "CHANL5"), (" a0b1ol ", "A0B1OL"), ("123456", "123456"), ("ABCDEF", "ABCDEF")])
    def test_any_six_letters_and_digits_are_allowed(self, code, expected):
        assert b2c.normalise_partner_code(code) == expected


class TestPartnerOperations:
    def test_create_inserts_the_cleaned_values_and_returns_the_partner(self):
        cur = Script([("SELECT 1 AS hit FROM b2c_partner", None), ("INSERT INTO b2c_partner", {"partner_id": 5}),
                      ("WHERE p.partner_id = %s", db_partner())])
        out = b2c.create_partner(cur, BODY, "root@example.com")
        sql, params = cur.find("INSERT INTO b2c_partner")[0]
        assert "ON CONFLICT (code) DO NOTHING" in sql
        assert params[0] == "CHANL5" and params[1] == "youtube" and params[-1] == "root@example.com"
        assert out["partner"]["code"] == "CHANL5" and out["partner"]["status"] == "active"

    def test_a_code_already_used_by_a_partner_or_a_customer_is_a_409(self):
        cur = Script([("SELECT 1 AS hit FROM b2c_partner", {"hit": 1})])
        with pytest.raises(HTTPException) as e:
            b2c.create_partner(cur, BODY)
        assert e.value.status_code == 409 and cur.find("INSERT INTO b2c_partner") == []
        assert "org_referral_code" in cur.find("SELECT 1 AS hit")[0][0]            # the customer codes are checked in the same query

    def test_a_race_on_the_unique_key_is_a_409_too(self):
        cur = Script([("SELECT 1 AS hit FROM b2c_partner", None), ("INSERT INTO b2c_partner", None)])
        with pytest.raises(HTTPException) as e:
            b2c.create_partner(cur, BODY)
        assert e.value.status_code == 409

    def test_the_code_cannot_be_changed_but_the_same_code_is_accepted(self):
        cur = Script([("FOR NO KEY UPDATE", {"code": "CHANL5", "status": 1}), ("WHERE p.partner_id = %s", db_partner())])
        with pytest.raises(HTTPException) as e:
            b2c.update_partner(cur, 5, {**BODY, "code": "OTHER9"})
        assert e.value.status_code == 400 and "cannot be changed" in e.value.detail
        assert cur.find("UPDATE b2c_partner") == []
        b2c.update_partner(cur, 5, {**BODY, "code": "chanl5"})
        assert len(cur.find("UPDATE b2c_partner")) == 1

    def test_status_maps_to_the_flag_and_defaults_to_the_current_one(self):
        cur = Script([("FOR NO KEY UPDATE", {"code": "CHANL5", "status": 1}), ("WHERE p.partner_id = %s", db_partner())])
        b2c.update_partner(cur, 5, {**BODY, "code": None, "status": "inactive"})
        assert cur.find("UPDATE b2c_partner")[0][1][12] == 0
        b2c.update_partner(cur, 5, {**BODY, "code": None})
        assert cur.find("UPDATE b2c_partner")[1][1][12] == 1
        with pytest.raises(HTTPException):
            b2c.update_partner(cur, 5, {**BODY, "code": None, "status": "paused"})

    def test_update_of_an_unknown_partner_is_404(self):
        with pytest.raises(HTTPException) as e:
            b2c.update_partner(Script([]), 99, BODY)
        assert e.value.status_code == 404

    def test_the_view_has_the_money_columns_and_due_is_never_negative(self):
        v = b2c._partner_view(db_partner())
        assert (v["signups"], v["converted"], v["revenue"], v["commission_earned"], v["commission_paid"], v["commission_due"]) == (
            4, 2, 12000.0, 700.0, 200.0, 500.0)
        assert b2c._partner_view(db_partner(commission_paid=D("900.00")))["commission_due"] == 0.0
        assert "phone" in v and v["created_at"].endswith("Z") and v["commission_type"] == "percent"


class TestPartnerList:
    ROWS = [db_partner(partner_id=2, code="BBBB22", channel_name="Insta Queen", platform="instagram", status=0, signups=1, converted=0,
                       commission_earned=D("0"), revenue=D("0"), commission_paid=D("0")),
            db_partner(partner_id=1)]

    def listing(self, **kw):
        cur = Script([("ORDER BY p.created_at DESC", self.ROWS), ("FILTER (WHERE rw.status = 'USED')", {"discount_given": D("1080.00")})])
        return b2c.list_partners(cur, **kw)

    def test_the_summary_covers_everyone_whatever_the_filter(self):
        out = self.listing(status="active")
        assert [p["code"] for p in out["partners"]] == ["CHANL5"]
        assert out["summary"] == {"partners": 2, "active": 1, "signups": 5, "converted": 2, "revenue": 12000.0, "discount_given": 1080.0,
                                  "commission_earned": 700.0, "commission_paid": 200.0, "commission_due": 500.0}

    def test_search_looks_in_the_code_channel_platform_and_contact(self):
        assert [p["code"] for p in self.listing(q="insta")["partners"]] == ["BBBB22"]
        assert [p["code"] for p in self.listing(q="YOUTUBE")["partners"]] == ["CHANL5"]
        assert [p["code"] for p in self.listing(q="bbbb")["partners"]] == ["BBBB22"]
        assert self.listing(q="nothing")["partners"] == []

    def test_a_bad_status_filter_is_a_400(self):
        with pytest.raises(HTTPException):
            self.listing(status="deleted")


class TestPartnerDetail:
    def detail(self, customers, months=(), payouts=(), partner=None):
        cur = Script([("WHERE p.partner_id = %s", partner or db_partner(signups=len(customers))),
                      ("FROM referral r LEFT JOIN org_info o", list(customers)),
                      ("GROUP BY 1", list(months)),
                      ("FROM b2c_payout WHERE partner_id", list(payouts))])
        return b2c.partner_detail(cur, 5, today=date(2026, 11, 15))

    def test_insights_customers_and_the_commission_ledger(self):
        customers = [
            {"referral_id": 3, "org_id": "o3", "org_name": "C", "created_at": datetime(2026, 11, 10), "qualified_at": None,
             "commission_amount": None, "rw_status": "AVAILABLE", "rw_expires_at": datetime(2020, 1, 1), "used_discount_amount": None,
             "first_base": None, "first_amount": None, "plan_name": None},
            {"referral_id": 2, "org_id": "o2", "org_name": "B", "created_at": datetime(2026, 10, 5), "qualified_at": datetime(2026, 10, 7),
             "commission_amount": D("500.00"), "rw_status": "USED", "rw_expires_at": None, "used_discount_amount": D("1080.00"),
             "first_base": None, "first_amount": D("7080.00"), "plan_name": "Prime"},
            {"referral_id": 1, "org_id": "o1", "org_name": "A", "created_at": datetime(2026, 9, 1), "qualified_at": datetime(2026, 9, 2),
             "commission_amount": D("200.00"), "rw_status": "USED", "rw_expires_at": None, "used_discount_amount": D("500.00"),
             "first_base": D("2000.00"), "first_amount": D("2360.00"), "plan_name": "Prime"},
        ]
        out = self.detail(customers, months=[{"month": "2026-10", "revenue": D("6000.00")}, {"month": "2019-01", "revenue": D("9")}],
                          payouts=[{"payout_id": 7, "amount": D("200.00"), "paid_on": date(2026, 11, 1), "reference": "UTR1", "note": None,
                                    "created_by": "root", "created_at": datetime(2026, 11, 1, 10)}])
        ins = out["insights"]
        assert (ins["signups"], ins["converted"], ins["conversion_rate"]) == (3, 2, 66.7)
        assert (ins["rewards_used"], ins["rewards_expired"], ins["discount_given"]) == (2, 1, 1580.0)
        assert (ins["commission_earned"], ins["commission_paid"], ins["commission_due"]) == (700.0, 200.0, 500.0)
        assert ins["last_signup_at"] == "2026-11-10T00:00:00Z" and ins["plans"] == [{"plan_name": "Prime", "customers": 2}]
        months = [m["month"] for m in ins["monthly"]]
        assert months == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
        by = {m["month"]: m for m in ins["monthly"]}
        assert (by["2026-09"]["signups"], by["2026-09"]["converted"]) == (1, 1) and by["2026-10"]["revenue"] == 6000.0
        assert (by["2026-11"]["signups"], by["2026-11"]["converted"]) == (1, 0)
        c = {x["org_id"]: x for x in out["customers"]}
        assert [x["org_id"] for x in out["customers"]] == ["o3", "o2", "o1"]
        assert c["o3"]["reward_status"] == "expired" and c["o3"]["status"] == "signed_up" and c["o3"]["first_payment"] is None
        assert c["o2"]["first_payment"] == 6000.0                                # no stored base: 7080 / 1.18
        assert c["o1"]["first_payment"] == 2000.0 and c["o1"]["discount_amount"] == 500.0 and c["o1"]["commission_amount"] == 200.0
        assert out["payouts"] == [{"payout_id": 7, "amount": 200.0, "paid_on": "2026-11-01", "reference": "UTR1", "note": None,
                                   "created_by": "root", "created_at": "2026-11-01T10:00:00Z", "status": "paid", "voided_at": None,
                                   "voided_by": None, "void_reason": None}]

    def test_a_partner_with_no_customers(self):
        out = self.detail([], partner=db_partner(signups=0, converted=0, commission_earned=D(0), commission_paid=D(0), revenue=D(0)))
        assert out["insights"]["conversion_rate"] == 0.0 and out["insights"]["last_signup_at"] is None and out["customers"] == []
        assert len(out["insights"]["monthly"]) == 6

    def test_an_unknown_partner_is_404(self):
        with pytest.raises(HTTPException) as e:
            b2c.partner_detail(Script([]), 99)
        assert e.value.status_code == 404

    def test_month_keys_cross_a_year_boundary(self):
        assert b2c._month_keys(6, date(2027, 2, 10)) == ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]


class TestPayouts:
    TODAY = date(2026, 11, 15)

    def pay(self, data, due="500.00", admin="root"):
        row = db_partner(commission_earned=D("700.00"), commission_paid=D("700.00") - D(due))
        cur = Script([("FOR NO KEY UPDATE", {"partner_id": 5}), ("WHERE p.partner_id = %s", row),
                      ("INSERT INTO b2c_payout", lambda p: {"payout_id": 9, "amount": p[1], "paid_on": p[2], "reference": p[3],
                                                            "note": p[4], "created_by": p[5], "created_at": datetime(2026, 11, 15, 9)})])
        return cur, b2c.record_payout(cur, 5, data, admin, today=self.TODAY)

    def test_a_payout_up_to_the_amount_due_is_recorded(self):
        cur, out = self.pay({"amount": "500", "paid_on": "2026-11-14", "reference": " UTR9 ", "note": ""})
        assert out["payout"] == {"payout_id": 9, "amount": 500.0, "paid_on": "2026-11-14", "reference": "UTR9", "note": None,
                                 "created_by": "root", "created_at": "2026-11-15T09:00:00Z", "status": "paid", "voided_at": None,
                                 "voided_by": None, "void_reason": None}
        assert "FOR NO KEY UPDATE" in cur.find("FOR NO KEY UPDATE")[0][0]          # two payouts at once cannot both pass the check
        assert out["partner"]["code"] == "CHANL5"

    def test_more_than_the_amount_due_is_refused(self):
        with pytest.raises(HTTPException) as e:
            self.pay({"amount": "500.01", "paid_on": "2026-11-14"})
        assert e.value.status_code == 400 and "more than the commission due" in e.value.detail

    @pytest.mark.parametrize("data,message", [
        ({"amount": "0", "paid_on": "2026-11-14"}, "more than zero"), ({"amount": "-5", "paid_on": "2026-11-14"}, "more than zero"),
        ({"amount": "abc", "paid_on": "2026-11-14"}, "must be a number"), ({"amount": "Infinity", "paid_on": "2026-11-14"}, "must be a number"),
        ({"amount": "NaN", "paid_on": "2026-11-14"}, "must be a number"), ({"amount": None, "paid_on": "2026-11-14"}, "must be a number"),
        ({"amount": "10", "paid_on": "14-11-2026"}, "YYYY-MM-DD"), ({"amount": "10", "paid_on": ""}, "YYYY-MM-DD"),
        ({"amount": "10", "paid_on": "2026-12-25"}, "future"), ({"amount": "10", "paid_on": "2026-11-14", "reference": "x" * 101}, "at most 100"),
    ])
    def test_bad_input_is_a_400(self, data, message):
        with pytest.raises(HTTPException) as e:
            self.pay(data)
        assert e.value.status_code == 400 and message in e.value.detail

    def test_an_unknown_partner_is_404(self):
        with pytest.raises(HTTPException) as e:
            b2c.record_payout(Script([]), 99, {"amount": "10", "paid_on": "2026-11-14"}, today=self.TODAY)
        assert e.value.status_code == 404

    def test_nothing_due_means_no_payout(self):
        with pytest.raises(HTTPException):
            self.pay({"amount": "1", "paid_on": "2026-11-14"}, due="0.00")


class TestVoidPayout:
    VOIDED = {"payout_id": 7, "amount": D("200.00"), "paid_on": date(2026, 11, 1), "reference": "UTR1", "note": None, "created_by": "root",
              "created_at": datetime(2026, 11, 1, 10), "voided_at": datetime(2026, 11, 15, 9), "voided_by": "boss", "void_reason": "typo"}

    def void(self, data=None, update=VOIDED, existing=None, partner=True):
        cur = Script([("FOR NO KEY UPDATE", {"partner_id": 5} if partner else None),
                      ("UPDATE b2c_payout SET voided_at", update),
                      ("SELECT voided_at FROM b2c_payout", existing),
                      ("WHERE p.partner_id = %s", db_partner(commission_paid=D("0.00")))])
        return cur, b2c.void_payout(cur, 5, 7, data or {}, "boss")

    def test_a_payout_is_voided_with_who_when_and_why(self):
        cur, out = self.void({"reason": "  typo  "})
        sql, params = cur.find("UPDATE b2c_payout SET voided_at")[0]
        assert "voided_at IS NULL" in sql and params == ("boss", "typo", 7, 5)         # only a payout that is still valid, of THIS partner
        assert out["payout"]["status"] == "voided" and out["payout"]["voided_by"] == "boss" and out["payout"]["void_reason"] == "typo"
        assert out["payout"]["voided_at"].endswith("Z") and out["partner"]["code"] == "CHANL5"

    def test_the_reason_is_optional(self):
        cur, _ = self.void({})
        assert cur.find("UPDATE b2c_payout SET voided_at")[0][1] == ("boss", None, 7, 5)

    def test_a_long_reason_is_a_400(self):
        with pytest.raises(HTTPException) as e:
            self.void({"reason": "x" * 301})
        assert e.value.status_code == 400

    def test_voiding_twice_is_a_409(self):
        with pytest.raises(HTTPException) as e:
            self.void(update=None, existing={"voided_at": datetime(2026, 11, 15)})
        assert e.value.status_code == 409 and "already voided" in e.value.detail

    def test_an_unknown_payout_or_partner_is_a_404(self):
        with pytest.raises(HTTPException) as e:
            self.void(update=None, existing=None)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            self.void(partner=False)
        assert e.value.status_code == 404

    def test_the_view_marks_a_voided_row_and_a_valid_one(self):
        assert b2c._payout_view(self.VOIDED)["status"] == "voided"
        assert b2c._payout_view({**self.VOIDED, "voided_at": None, "voided_by": None, "void_reason": None})["status"] == "paid"

    def test_voided_payouts_do_not_count_as_paid(self):
        assert "WHERE voided_at IS NULL" in " ".join(b2c._PARTNERS_SQL.split())           # commission_paid ignores them, due goes up


# ═════════════════════════════════════════════════════════════════════════ routes and guards
class TestRouteGuards:
    def op(self):
        import os
        os.environ["OPERATOR_ENFORCE"] = "1"
        operator_auth.set_lookup(lambda phone: {"1": 1, "2": 2}.get(phone))

    def test_reads_are_for_any_operator_and_writes_for_a_super_admin_only(self, env, monkeypatch):
        self.op()
        calls = []

        async def fake(*args, **kwargs):
            calls.append(args)
            return {"ok": True}

        from router import gen_dashboard as gd
        for name in ("operator_list_partners", "operator_create_partner", "operator_update_partner", "operator_partner_detail",
                     "operator_record_payout", "operator_void_payout"):
            monkeypatch.setattr(gd.b2c, name, fake)
        body = {"platform": "youtube", "channel_name": "X", "discount_percent": 10, "code": "ABCD12"}
        reads = [("get", "/gen-dashboard/referrals/b2c", None), ("get", "/gen-dashboard/referrals/b2c/5", None)]
        writes = [("post", "/gen-dashboard/referrals/b2c", body), ("put", "/gen-dashboard/referrals/b2c/5", body),
                  ("post", "/gen-dashboard/referrals/b2c/5/payouts", {"amount": 1, "paid_on": "2026-11-14"}),
                  ("post", "/gen-dashboard/referrals/b2c/5/payouts/7/void", {"reason": "typo"})]
        for verb, url, payload in reads:
            c = getattr(env.client, verb)
            assert c(url).status_code == 403                                         # no credential
            assert c(url, headers={"x-admin-id": "2"}).status_code == 200            # any operator may read
        for verb, url, payload in writes:
            c = getattr(env.client, verb)
            assert c(url, json=payload).status_code == 403
            assert c(url, json=payload, headers={"x-admin-id": "2"}).status_code == 403   # role 2 is read only
            assert c(url, json=payload, headers={"x-admin-id": "1"}).status_code == 200
        assert len(calls) == 2 + 4

    def test_the_admin_identity_is_recorded_as_the_creator(self, env, monkeypatch):
        self.op()
        seen = {}

        async def create(data, admin=None):
            seen["admin"] = admin
            return {"partner": {}}

        from router import gen_dashboard as gd
        monkeypatch.setattr(gd.b2c, "operator_create_partner", create)
        env.client.post("/gen-dashboard/referrals/b2c", json={"platform": "youtube"}, headers={"x-admin-id": "1"})
        assert seen["admin"] == "1"                                                  # the operator's identity, not the guard's label

    def test_an_app_token_is_not_an_operator_session(self, env):
        self.op()
        assert env.client.get("/gen-dashboard/referrals/b2c", headers={"authorization": "Bearer abc"}).status_code == 401


class TestRouteHandlersReachTheFunctions:
    def test_the_async_helpers_commit_on_write_and_roll_back_on_a_400(self, monkeypatch):
        events = []

        class Conn:
            def cursor(self, cursor_factory=None): return Script([("SELECT 1 AS hit FROM b2c_partner", {"hit": 1})])
            def commit(self): events.append("commit")
            def rollback(self): events.append("rollback")

        monkeypatch.setattr(b2c, "get_db_connection", lambda: Conn())
        monkeypatch.setattr(b2c, "return_connection", lambda c: events.append("released"))
        with pytest.raises(HTTPException) as e:
            asyncio.run(b2c.operator_create_partner(BODY))
        assert e.value.status_code == 409 and events == ["rollback", "released"]
        events.clear()
        assert asyncio.run(b2c.operator_list_partners())["summary"]["partners"] == 0
        assert events == ["released"]                                                # a read never commits
