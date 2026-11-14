"""DB-free tests of the billing HTTP flow: POST /orders/quote, /orders/create, /payments/verify and /webhook/razorpay.

A small stateful fake database (orders, subscriptions, payments, invoices, webhook events) stands in for PostgreSQL so
idempotency (verify twice, webhook twice, webhook-before-verify, verify-before-webhook), the amount-mismatch guard,
the server-side recompute and the exact SQL predicates can be asserted. Razorpay and e-mail are monkeypatched.
Run: python -m pytest tests/test_orders_billing.py -q
"""
import hashlib
import hmac
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import gen_orders_functions as orders_fn  # noqa: E402
from core_functions import gen_payment_functions as pay_fn  # noqa: E402
from core_functions import gen_webhook_functions as hook_fn  # noqa: E402
from router import gen_orders, gen_payments, gen_webhook  # noqa: E402
from utils import razorpay as razorpay_util  # noqa: E402

D = Decimal
TODAY = date(2026, 11, 2)
SECRET = "whsec_test"
ADMIN, ORG = "user-1", "org-1"


def norm(sql):
    return " ".join(sql.split())


class FakeDB:
    """Stateful stand-in for the handful of statements the billing code runs."""

    def __init__(self):
        self.users = {ADMIN: {"user_id": ADMIN, "name": "Asha", "email": "asha@example.com", "mobile_number": "9000000000"}}
        self.orgs = {ORG: {"org_id": ORG, "org_name": "Acme", "parent_id": ADMIN, "state": "Tamil Nadu", "org_address": "1 Road",
                           "district": "Chennai", "pincode": 600001}}
        self.plans = {
            1: {"plan_id": 1, "plan_name": "Core", "plan_amount": D("25.00"), "plan_month": 6},
            2: {"plan_id": 2, "plan_name": "Prime", "plan_amount": D("40.00"), "plan_month": 12},
            3: {"plan_id": 3, "plan_name": "Elite", "plan_amount": D("40.00"), "plan_month": 12},
            # other durations of the same plans, with free months (the rows above have none: free_months is absent = 0)
            11: {"plan_id": 11, "plan_name": "Core", "plan_amount": D("25.00"), "plan_month": 12, "free_months": 2},
            12: {"plan_id": 12, "plan_name": "Prime", "plan_amount": D("40.00"), "plan_month": 6, "free_months": 1},
            13: {"plan_id": 13, "plan_name": "Prime", "plan_amount": D("40.00"), "plan_month": 12, "free_months": 2},
        }
        self.coupons = {}
        self.subs = []          # newest last
        self.orders = []
        self.payments = []
        self.invoices = []
        self.events = []
        self.free_trial = []
        # Refer & Earn (core_functions/referral_functions.py)
        self.codes = {}         # org_id -> code
        self.referrals = []     # {referral_id, referrer_org_id, referred_org_id, code, created_at, qualified_at}
        self.rewards = []       # {reward_id, referral_id, org_id, role, status, percent, created_at, available_at, used_*}
        self.seq = 0
        self.fail_invoice_insert = False
        self.log = []           # (sql, params)
        self.commits = 0
        self.rollbacks = 0

    # helpers for tests
    def add_sub(self, plan_id, end, staff, status="ACTIVE", sid=None):
        self.subs.append({"id": len(self.subs) + 1, "org_id": ORG, "user_id": ADMIN, "plan_id": plan_id, "status": status,
                          "start_date": date(2026, 3, 1), "end_date": end, "staff_limit": staff,
                          "subscription_id": sid or f"old-{len(self.subs)}"})

    def add_order(self, oid="order_1", plan_id=2, amount=D("8496.00"), mode="new", staff=15, term=12, status="CREATED",
                  base=D("7200.00"), gst=D("1296.00"), **kw):
        o = {"id": len(self.orders) + 1, "user_id": ADMIN, "org_id": ORG, "plan_id": plan_id, "razorpay_order_id": oid,
             "amount": amount, "currency": "INR", "status": status, "staff_limit": staff, "coupon_code": None,
             "purchase_mode": mode, "term_months": term, "base_amount": base, "gst_amount": gst, "payment_flag": None,
             "activated_at": None, "discount_amount": D("0.00"), "referral_reward_id": None}
        o.update(kw)
        self.orders.append(o)
        return o

    def active(self):
        return [s for s in self.subs if s["status"] == "ACTIVE"]


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._one = None
        self._all = []
        self.rowcount = 0

    def close(self):
        pass

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def _set(self, one=None, rowcount=None):
        self._one = one
        self._all = [] if one is None else [one]
        self.rowcount = rowcount if rowcount is not None else (1 if one is not None else 0)

    def execute(self, sql, params=None):
        db = self.db
        s = norm(sql)
        p = tuple(params or ())
        db.log.append((s, p))
        self.rowcount = 0
        self._one = None
        self._all = []

        if s.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            return
        if s == "SELECT user_id FROM users WHERE user_id = %s":
            return self._set(db.users.get(p[0]))
        if "FROM org_info o WHERE o.org_id = %s AND (o.parent_id = %s" in s:
            o = db.orgs.get(p[0])
            u = db.users.get(p[2]) or {}
            ok = o and (o["parent_id"] == p[1] or u.get("org_id") == o["org_id"])
            return self._set({"ok": 1} if ok else None)
        if s == "SELECT 1 AS ok FROM org_info WHERE org_id = %s LIMIT 1":
            return self._set({"ok": 1} if p[0] in db.orgs else None)
        if "FROM subscription ps JOIN gen_plans gp" in s:
            rows = [x for x in db.subs if x["org_id"] == p[0] and x["status"] in ("ACTIVE", "EXPIRED")]
            if not rows:
                return self._set(None)
            r = dict(rows[-1])
            r.update(db.plans[r["plan_id"]])
            return self._set(r)
        if s.startswith("SELECT plan_id, plan_name, plan_amount, plan_month, free_months FROM gen_plans WHERE plan_id = %s"):
            return self._set(db.plans.get(p[0]))
        if s == "SELECT plan_name, plan_month FROM gen_plans WHERE plan_id = %s":
            return self._set(db.plans.get(p[0]))
        if "FROM gen_coupons" in s and "UPPER(coupon_name) = UPPER(%s)" in s:
            c = db.coupons.get(p[0].upper())
            return self._set(c)
        if "u.name AS customer_name" in s and "gp.plan_name" in s:
            u = db.users[p[2]]
            o = db.orgs.get(p[0], {})
            gp = db.plans.get(p[1], {})
            return self._set({"customer_name": u["name"], "phone_number": u["mobile_number"], "email": u["email"],
                              "org_name": o.get("org_name"), "plan_name": gp.get("plan_name")})
        if s.startswith("SELECT COUNT(*) AS cnt FROM gen_orders"):
            return self._set({"cnt": len([o for o in db.orders if o["status"] == "PAID" and o["user_id"] == p[0]])})
        if s.startswith("INSERT INTO gen_orders"):
            keys = ["user_id", "org_id", "plan_id", "razorpay_order_id", "amount", "currency", "status", "staff_limit", "coupon_code",
                    "purchase_mode", "term_months", "base_amount", "gst_amount", "discount_amount", "credit_amount",
                    "client_amount", "pricing", "referral_reward_id"]
            assert len(p) == len(keys) == s.split("VALUES")[1].count("%s")
            row = dict(zip(keys, p))
            row.update({"id": len(db.orders) + 1, "payment_flag": None, "activated_at": None})
            db.orders.append(row)
            return self._set(None, rowcount=1)

        # ── activation ────────────────────────────────────────────────────
        if s.startswith("SELECT id, user_id, org_id, plan_id, razorpay_order_id") and "FOR UPDATE" in s:
            rows = [o for o in db.orders if o["razorpay_order_id"] == p[0] and (len(p) == 1 or o["user_id"] == p[1])]
            return self._set(dict(rows[0]) if rows else None)
        if s.startswith("SELECT plan_id, end_date, staff_limit FROM subscription WHERE subscription_id = %s"):
            rows = [x for x in db.subs if x["subscription_id"] == p[0]]
            return self._set(dict(rows[0]) if rows else None)
        if s.startswith("INSERT INTO gen_payments"):
            if any(x["razorpay_payment_id"] == p[2] for x in db.payments):
                return self._set(None, rowcount=0)
            db.payments.append({"user_id": p[0], "org_id": p[1], "razorpay_payment_id": p[2], "razorpay_order_id": p[3], "amount": p[4]})
            return self._set(None, rowcount=1)
        if s.startswith("INSERT INTO subscription"):
            if any(x["subscription_id"] == p[3] for x in db.subs):
                return self._set(None, rowcount=0)
            db.subs.append({"id": len(db.subs) + 1, "org_id": p[0], "user_id": p[1], "plan_id": p[2], "subscription_id": p[3],
                            "status": "ACTIVE", "start_date": p[4], "end_date": p[5], "staff_limit": p[6]})
            return self._set(None, rowcount=1)
        if s.startswith("UPDATE subscription SET status = 'INACTIVE'"):
            n = 0
            for x in db.subs:
                if x["org_id"] == p[0] and x["subscription_id"] != p[1] and x["status"] != "INACTIVE":
                    x["status"] = "INACTIVE"
                    n += 1
            return self._set(None, rowcount=n)
        if s.startswith("UPDATE free_trial"):
            for t in db.free_trial:
                if t["org_id"] == p[1] and t["is_active"]:
                    t["is_active"] = False
                    t["end_date"] = min(t["end_date"], p[0])
            return self._set(None, rowcount=1)
        if s.startswith("UPDATE gen_orders SET status = 'PAID'"):
            for o in db.orders:
                if o["id"] == p[0]:
                    o["status"] = "PAID"
                    o["activated_at"] = o["activated_at"] or datetime(2026, 11, 2)
            return self._set(None, rowcount=1)
        if s.startswith("UPDATE gen_orders SET payment_flag = 'AMOUNT_MISMATCH'"):
            for o in db.orders:
                if o["id"] == p[0]:
                    o["payment_flag"] = "AMOUNT_MISMATCH"
            return self._set(None, rowcount=1)
        if s.startswith("UPDATE gen_orders SET payment_flag = 'REFERRAL_ALREADY_USED'"):
            for o in db.orders:
                if o["id"] == p[0]:
                    o["payment_flag"] = "REFERRAL_ALREADY_USED"
            return self._set(None, rowcount=1)
        if s.startswith("UPDATE gen_orders SET status = 'FAILED'"):
            n = 0
            for o in db.orders:
                if o["razorpay_order_id"] == p[0] and o["status"] != "PAID" and o["activated_at"] is None:
                    o["status"] = "FAILED"
                    n += 1
            return self._set(None, rowcount=n)
        if "u.email AS customer_email" in s:
            u = db.users[p[1]]
            o = db.orgs.get(p[0], {})
            return self._set({"customer_name": u["name"], "customer_email": u["email"], "phone_number": u["mobile_number"],
                              "org_name": o.get("org_name"), "org_address": o.get("org_address"), "state": o.get("state"),
                              "district": o.get("district"), "pincode": o.get("pincode")})
        if s == "SELECT invoice_number FROM gen_invoices WHERE razorpay_payment_id = %s":
            rows = [i for i in db.invoices if i["razorpay_payment_id"] == p[0]]
            return self._set(dict(rows[0]) if rows else None)
        if s == "SELECT nextval('gen_invoice_seq') AS n":
            db.seq += 1
            return self._set({"n": db.seq})
        if s.startswith("INSERT INTO gen_invoices"):
            if db.fail_invoice_insert:
                raise RuntimeError("invoice insert failed")
            keys = ["invoice_id", "invoice_number", "user_id", "org_id", "razorpay_payment_id", "razorpay_order_id", "plan_name",
                    "staff_count", "base_amount", "cgst", "sgst", "igst", "total_amount", "payment_date", "valid_till", "coupon_code",
                    "referral_reward_id"]
            assert len(p) == len(keys) == s.split("VALUES")[1].split("ON CONFLICT")[0].count("%s")
            row = dict(zip(keys, p))
            if any(i["razorpay_payment_id"] == row["razorpay_payment_id"] or i["invoice_number"] == row["invoice_number"] for i in db.invoices):
                return self._set(None, rowcount=0)
            db.invoices.append(row)
            return self._set({"invoice_number": row["invoice_number"]}, rowcount=1)

        # ── Refer & Earn ──────────────────────────────────────────────────
        if s.startswith("SELECT reward_id, percent, COUNT(*) OVER () AS available_count FROM referral_reward"):
            rows = sorted((r for r in db.rewards if r["org_id"] == p[0] and r["status"] == "AVAILABLE"),
                          key=lambda r: (r["available_at"] or r["created_at"], r["reward_id"]))
            if not rows:
                return self._set(None)
            return self._set({"reward_id": rows[0]["reward_id"], "percent": rows[0]["percent"], "available_count": len(rows)})
        if s.startswith("UPDATE referral_reward SET status = 'USED'"):
            order_id, invoice_number, plan_name, discount, reward_id, org_id = p
            for r in db.rewards:
                if r["reward_id"] == reward_id and r["org_id"] == org_id and r["status"] == "AVAILABLE":
                    r.update(status="USED", used_at=datetime(2026, 11, 2), used_order_id=order_id, used_invoice_number=invoice_number,
                             used_plan_name=plan_name, used_discount_amount=discount)
                    return self._set(None, rowcount=1)
            return self._set(None, rowcount=0)
        if s.startswith("UPDATE referral_reward SET used_invoice_number = %s WHERE reward_id = %s AND used_order_id = %s"):
            n = 0
            for r in db.rewards:
                if r["reward_id"] == p[1] and r.get("used_order_id") == p[2] and r["status"] == "USED":
                    r["used_invoice_number"] = p[0]
                    n += 1
            return self._set(None, rowcount=n)
        if s.startswith("UPDATE referral SET qualified_at = NOW() WHERE referred_org_id = %s AND qualified_at IS NULL"):
            for ref in db.referrals:
                if ref["referred_org_id"] == p[0] and ref["qualified_at"] is None:
                    ref["qualified_at"] = datetime(2026, 11, 2)
                    return self._set({"referral_id": ref["referral_id"]}, rowcount=1)
            return self._set(None, rowcount=0)
        if s.startswith("UPDATE referral_reward SET status = 'AVAILABLE', available_at = NOW() WHERE referral_id = %s AND role = 'referrer'"):
            n = 0
            for r in db.rewards:
                if r["referral_id"] == p[0] and r["role"] == "referrer" and r["status"] == "PENDING":
                    r.update(status="AVAILABLE", available_at=datetime(2026, 11, 2))
                    n += 1
            return self._set(None, rowcount=n)

        # ── webhook events ────────────────────────────────────────────────
        if s.startswith("INSERT INTO gen_webhook_events"):
            if p[0] is not None and any(e["event_id"] == p[0] for e in db.events):
                return self._set(None, rowcount=0)
            db.events.append({"event_id": p[0], "event_type": p[1]})
            return self._set({"id": len(db.events)}, rowcount=1)
        raise AssertionError(f"unexpected SQL: {s}")


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1


class FakeRazorpay:
    def __init__(self):
        self.created = []

    def create_order(self, amount, receipt=None, notes=None):
        self.created.append({"amount": amount, "notes": notes})
        return {"id": f"order_srv_{len(self.created)}", "currency": "INR"}

    def verify_payment_signature(self, order_id, payment_id, signature):
        return signature == "good-signature"


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    rz = FakeRazorpay()
    sent = []
    for mod in (orders_fn, pay_fn, hook_fn):
        monkeypatch.setattr(mod, "get_db_connection", lambda: FakeConn(db))
        monkeypatch.setattr(mod, "return_connection", lambda c: None)
    for mod in (orders_fn, pay_fn):
        monkeypatch.setattr(mod, "get_razorpay_client", lambda: (rz, "rzp_test_key"))
        monkeypatch.setattr(mod, "ist_today", lambda: TODAY)
    monkeypatch.setattr(pay_fn, "send_invoice_email", lambda email, data: sent.append((email, data)))
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SELLER_PLACE_OF_SUPPLY", "Tamil Nadu (33)")
    app = FastAPI()
    app.include_router(gen_orders.router)
    app.include_router(gen_payments.router)
    app.include_router(gen_webhook.router)
    client = TestClient(app)
    return type("Env", (), {"db": db, "rz": rz, "sent": sent, "client": client})()


def create_body(**kw):
    body = {"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "amount": 8496, "staff_limit": 15, "coupon_code": None}
    body.update(kw)
    return body


def sign(body: bytes, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def hook(env, event, order_id="order_1", payment_id="pay_1", amount=849600, currency="INR", event_id="evt_1", raw=None, secret=SECRET,
         signature=None):
    payload = {"event": event, "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": amount,
                                                                 "currency": currency, "status": "captured"}}}}
    raw = raw if raw is not None else json.dumps(payload).encode()
    headers = {"X-Razorpay-Signature": signature if signature is not None else sign(raw, secret), "Content-Type": "application/json"}
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return env.client.post("/webhook/razorpay", content=raw, headers=headers)


def verify(env, order_id="order_1", payment_id="pay_1", signature="good-signature", **kw):
    body = {"user_id": ADMIN, "razorpay_order_id": order_id, "razorpay_payment_id": payment_id, "razorpay_signature": signature}
    body.update(kw)
    return env.client.post("/payments/verify", json=body)


# ═════════════════════════════════════════════════════════════════════════
class TestQuote:
    def test_new_plan_quote(self, env):
        r = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "new"})
        assert r.status_code == 200
        q = r.json()["data"]
        assert r.json()["success"] is True
        assert (q["staff_charge"], q["gst"], q["grand_total"], q["amount_paise"]) == (7200.0, 1296.0, 8496.0, 849600)
        assert q["valid_till"] == "2027-11-02" and q["payable"] is True
        assert env.db.orders == []                       # nothing stored

    def test_prices_follow_the_plan_table(self, env):
        env.db.plans[2]["plan_amount"] = D("50.00")
        q = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 10, "mode": "new"}).json()["data"]
        assert q["staff_charge"] == 6000.0 and q["grand_total"] == 7080.0

    def test_coupon_from_the_database(self, env):
        env.db.coupons["SAVE10"] = {"coupon_id": 1, "coupon_name": "SAVE10", "discount": D("10.00"), "status": 1,
                                    "discount_type": "percent", "flat_amount": None, "max_discount": None, "min_amount": None,
                                    "valid_from": None, "valid_until": None, "plan_ids": None}
        q = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "new",
                                                   "coupon_code": "save10"}).json()["data"]
        assert q["grand_total"] == 7646.0 and q["coupon_applied"] is True

    def test_expired_coupon_is_reported_inside_the_quote(self, env):
        env.db.coupons["OLD"] = {"coupon_id": 2, "coupon_name": "OLD", "discount": D("10.00"), "status": 1, "discount_type": "percent",
                                 "flat_amount": None, "max_discount": None, "min_amount": None, "valid_from": None,
                                 "valid_until": date(2026, 1, 1), "plan_ids": None}
        r = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "new",
                                                   "coupon_code": "OLD"})
        q = r.json()["data"]
        assert r.status_code == 200 and q["coupon_applied"] is False and q["coupon_message"] and q["grand_total"] == 8496.0

    def test_renew_quote_for_an_active_plan(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 12)
        q = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "staff_limit": 10, "mode": "renew"}).json()["data"]
        assert q["grand_total"] == 3540.0 and q["valid_from"] == "2027-03-01" and q["valid_till"] == "2028-03-01" and q["plan_id"] == 1

    def test_upgrade_quote(self, env):
        env.db.add_sub(3, date(2027, 3, 1), 12)
        q = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "staff_limit": 8, "mode": "upgrade"}).json()["data"]
        assert q["grand_total"] == 1510.0 and q["resulting_staff_limit"] == 20

    # ── plans with free months: 6 months / 1 free charges 5 and is valid for 6 ─────────────────────────────────────────
    def quote(self, env, **body):
        r = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, **body})
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_new_plan_with_free_months_charges_the_paid_months_and_is_valid_for_the_whole_term(self, env):
        q = self.quote(env, plan_id=13, staff_limit=15, mode="new")             # Prime 12 months, 2 free, 40 / staff / month
        assert (q["charged_months"], q["term_months"], q["free_months"]) == (10, 12, 2)
        assert (q["staff_charge"], q["gst"], q["grand_total"]) == (6000.0, 1080.0, 7080.0)     # 15 x 40 x 10
        assert q["valid_till"] == "2027-11-02" and q["price_per_staff_month"] == 40.0          # the list price stays per month
        q = self.quote(env, plan_id=12, staff_limit=10, mode="new")             # Prime 6 months, 1 free
        assert (q["charged_months"], q["term_months"], q["free_months"]) == (5, 6, 1)
        assert (q["staff_charge"], q["grand_total"]) == (2000.0, 2360.0) and q["valid_till"] == "2027-05-02"

    def test_plans_without_free_months_are_priced_exactly_as_before(self, env):
        q = self.quote(env, plan_id=2, staff_limit=15, mode="new")
        assert (q["charged_months"], q["term_months"], q["free_months"], q["grand_total"]) == (12, 12, 0, 8496.0)

    def test_adding_staff_ignores_free_months(self, env):
        env.db.add_sub(13, date(2027, 3, 1), 12)                                # on the 12 month plan with 2 free months
        q = self.quote(env, staff_limit=8, mode="upgrade")
        assert q["grand_total"] == 1510.0 and q["resulting_staff_limit"] == 20  # same as the plan without free months
        assert (q["charged_months"], q["free_months"], q["term_months"]) == (4, 0, 0) and q["valid_till"] == "2027-03-01"

    def test_changing_plan_credits_the_paid_months_of_the_current_plan(self, env):
        env.db.add_sub(11, date(2027, 11, 2), 10)                               # Core 12 months, 2 free, 25: it cost 10 x 25 x 10
        q = self.quote(env, plan_id=13, staff_limit=10, mode="new")             # to Prime 12 months, 2 free
        assert q["credit"] == 2500.0 and q["staff_charge"] == 4000.0
        assert (q["after_credit"], q["gst"], q["grand_total"]) == (1500.0, 270.0, 1770.0)

    def test_credit_of_a_plan_without_free_months_is_unchanged(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 10)                                 # Core 6 months, no free months
        q = self.quote(env, plan_id=13, staff_limit=10, mode="new")
        assert q["credit"] == 1500.0                                            # 10 x 25 x 6

    def test_renewal_keeps_the_free_month_share(self, env):
        env.db.add_sub(11, date(2027, 3, 1), 12)                                # Core 12 months, 2 free
        q = self.quote(env, staff_limit=10, mode="renew")
        assert (q["charged_months"], q["term_months"], q["free_months"]) == (10, 12, 2)
        assert (q["staff_charge"], q["gst"], q["grand_total"]) == (2500.0, 450.0, 2950.0)
        assert q["valid_from"] == "2027-03-01" and q["valid_till"] == "2028-03-01"
        env.db.subs.clear()
        env.db.add_sub(12, date(2027, 3, 1), 12)                                # Prime 6 months, 1 free renews as 12 months, 2 free
        q = self.quote(env, staff_limit=10, mode="renew")
        assert (q["charged_months"], q["free_months"], q["grand_total"]) == (10, 2, 4720.0)    # 10 x 40 x 10 + 18 %

    def test_errors_are_real_status_codes(self, env):
        post = lambda **kw: env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15,
                                                                   "mode": "new", **kw})
        assert post(plan_id=99).status_code == 404
        assert post(staff_limit=0).status_code == 400
        assert post(staff_limit=9).status_code == 400
        assert post(mode="gift").status_code == 400
        assert post(user_id="ghost").status_code == 404
        assert post(org_id="other-org").status_code == 404
        assert env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "mode": "renew"}).status_code == 400   # no plan to renew
        assert env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "mode": "new"}).status_code == 400      # no plan_id

    def test_foreign_org_is_refused(self, env):
        env.db.orgs["org-2"] = {"org_id": "org-2", "org_name": "Other", "parent_id": "someone-else"}
        r = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": "org-2", "plan_id": 2, "staff_limit": 15, "mode": "new"})
        assert r.status_code == 403

    def test_owner_check_can_be_switched_off(self, env, monkeypatch):
        monkeypatch.setenv("BILLING_ENFORCE_ORG_OWNER", "0")
        env.db.orgs["org-2"] = {"org_id": "org-2", "org_name": "Other", "parent_id": "someone-else"}
        r = env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": "org-2", "plan_id": 2, "staff_limit": 15, "mode": "new"})
        assert r.status_code == 200

    def test_org_scoping_of_the_subscription_lookup(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 12)
        env.client.post("/orders/quote", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "new"})
        sql, params = [x for x in env.db.log if "FROM subscription ps JOIN gen_plans gp" in x[0]][-1]
        assert "ps.org_id = %s" in sql and params == (ORG,)


class TestCreateOrder:
    def test_amount_is_recomputed_and_a_tampered_amount_is_ignored(self, env, caplog):
        with caplog.at_level("WARNING"):
            r = env.client.post("/orders/create", json=create_body(amount=1, mode="new"))
        assert r.status_code == 200
        body = r.json()
        assert body["amount"] == 8496.0 and body["checkout"]["amount"] == 8496.0 and body["success"] is True
        assert env.rz.created[0]["amount"] == D("8496.00")                 # what Razorpay was asked to charge
        assert env.db.orders[0]["amount"] == D("8496.00") and env.db.orders[0]["client_amount"] == D("1.00")
        assert any("ignored" in m for m in caplog.messages)

    def test_response_shape_is_unchanged(self, env):
        body = env.client.post("/orders/create", json=create_body(mode="new")).json()
        for key in ("success", "is_first_payment", "order_id", "key_id", "amount", "currency", "order_name", "customer", "checkout"):
            assert key in body
        assert set(body["checkout"]) == {"order_id", "key_id", "amount", "currency", "prefill", "notes"}
        assert body["key_id"] == "rzp_test_key" and body["order_name"] == "Prime - Acme" and body["quote"]["grand_total"] == 8496.0

    def test_missing_amount_is_accepted(self, env):
        body = create_body(mode="new")
        body.pop("amount")
        assert env.client.post("/orders/create", json=body).status_code == 200

    def test_the_five_reference_totals(self, env):
        env.db.coupons["WELCOME10"] = {"coupon_id": 1, "coupon_name": "WELCOME10", "discount": D("10.00"), "status": 1,
                                       "discount_type": "percent", "flat_amount": None, "max_discount": None, "min_amount": None,
                                       "valid_from": None, "valid_until": None, "plan_ids": None}
        post = lambda **kw: env.client.post("/orders/create", json=create_body(amount=1, **kw)).json()
        assert post(mode="new")["amount"] == 8496.0
        assert post(mode="new", coupon_code="welcome10")["amount"] == 7646.0
        env.db.add_sub(1, date(2027, 3, 1), 12)
        assert post(mode="new", staff_limit=20, coupon_code="WELCOME10")["amount"] == 8602.0
        assert post(mode="renew", plan_id=1, staff_limit=10)["amount"] == 3540.0
        env.db.subs.clear()
        env.db.add_sub(3, date(2027, 3, 1), 12)
        assert post(mode="upgrade", plan_id=3, staff_limit=8)["amount"] == 1510.0

    def test_mode_is_inferred_for_older_builds_from_the_displayed_amount(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 12)
        r = env.client.post("/orders/create", json=create_body(plan_id=1, staff_limit=10, amount=3540)).json()
        assert r["quote"]["mode"] == "renew" and r["amount"] == 3540.0
        assert env.db.orders[-1]["purchase_mode"] == "renew" and env.db.orders[-1]["term_months"] == 12

    def test_order_snapshot_is_stored(self, env):
        env.client.post("/orders/create", json=create_body(mode="new"))
        o = env.db.orders[0]
        assert (o["purchase_mode"], o["term_months"], o["base_amount"], o["gst_amount"], o["status"], o["staff_limit"]) == (
            "new", 12, D("7200.00"), D("1296.00"), "CREATED", 15)

    def test_unusable_coupon_refuses_the_order(self, env):
        r = env.client.post("/orders/create", json=create_body(mode="new", coupon_code="NOPE"))
        assert r.status_code == 400 and r.json()["detail"] == "Invalid or expired coupon" and env.db.orders == []

    def test_zero_total_is_refused(self, env):
        env.db.coupons["FREE"] = {"coupon_id": 3, "coupon_name": "FREE", "discount": D("100.00"), "status": 1, "discount_type": "percent",
                                  "flat_amount": None, "max_discount": None, "min_amount": None, "valid_from": None,
                                  "valid_until": None, "plan_ids": None}
        r = env.client.post("/orders/create", json=create_body(mode="new", coupon_code="FREE"))
        assert r.status_code == 400 and r.json()["detail"] == "Amount must be greater than 0" and env.rz.created == []

    @pytest.mark.parametrize("staff,status", [(-5, 400), (0, 400), (5, 400), (10_001, 400)])
    def test_staff_guards(self, env, staff, status):
        assert env.client.post("/orders/create", json=create_body(mode="new", staff_limit=staff)).status_code == status
        assert env.rz.created == []

    def test_unknown_plan_and_user_and_foreign_org(self, env):
        assert env.client.post("/orders/create", json=create_body(plan_id=99, mode="new")).status_code == 404
        assert env.client.post("/orders/create", json=create_body(user_id="ghost")).status_code == 404
        env.db.orgs["org-2"] = {"org_id": "org-2", "org_name": "Other", "parent_id": "someone-else"}
        assert env.client.post("/orders/create", json=create_body(org_id="org-2")).status_code == 403

    def test_razorpay_failure_is_a_clean_error(self, env, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("secret key abc leaked")
        monkeypatch.setattr(env.rz, "create_order", boom)
        r = env.client.post("/orders/create", json=create_body(mode="new"))
        assert r.status_code == 502 and "abc" not in r.text and env.db.orders == []

    def test_razorpay_amount_is_in_paise_without_float_error(self):
        assert razorpay_util._paise(D("8602.00")) == 860200
        assert razorpay_util._paise(19.99) == 1999 and razorpay_util._paise("0.29") == 29


class TestVerify:
    def make_order(self, env, **kw):
        return env.db.add_order(**kw)

    def test_activates_a_new_plan_once(self, env):
        self.make_order(env)
        r = verify(env)
        assert r.status_code == 200
        b = r.json()
        assert b["success"] is True and b["message"] == "Payment verified successfully"
        assert b["valid_till"] == "2027-11-02" and b["plan_id"] == 2 and b["staff_limit"] == 15 and b["mode"] == "new"
        assert [s["status"] for s in env.db.subs] == ["ACTIVE"] and env.db.subs[0]["subscription_id"] == "sub-order_1"
        assert len(env.db.payments) == 1 and len(env.db.invoices) == 1
        assert env.db.orders[0]["status"] == "PAID" and env.db.orders[0]["activated_at"] is not None

    def test_second_verify_is_a_no_op_success(self, env):
        self.make_order(env)
        verify(env)
        r = verify(env)
        assert r.status_code == 200 and r.json()["message"] == "Payment already verified"
        assert r.json()["valid_till"] == "2027-11-02" and r.json()["plan_id"] == 2
        assert len(env.db.subs) == 1 and len(env.db.payments) == 1 and len(env.db.invoices) == 1 and len(env.sent) == 1

    def test_bad_signature(self, env):
        self.make_order(env)
        r = verify(env, signature="bad")
        assert r.status_code == 400 and r.json()["detail"] == "Invalid payment signature" and env.db.subs == []

    def test_unknown_order_and_other_users_order(self, env):
        assert verify(env, order_id="order_nope").status_code == 404
        self.make_order(env, user_id="someone-else")
        assert verify(env).status_code == 404            # the lookup also filters by the calling user
        sql, params = [x for x in env.db.log if "FOR UPDATE" in x[0]][-1]
        assert "AND user_id = %s" in sql and params == ("order_1", ADMIN)

    def test_renew_active_extends_from_the_end_date(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 25)
        self.make_order(env, plan_id=1, mode="renew", staff=10, term=12, amount=D("3540.00"), base=D("3000.00"), gst=D("540.00"))
        b = verify(env).json()
        assert b["valid_till"] == "2028-03-01" and b["staff_limit"] == 10
        assert [s["status"] for s in env.db.subs] == ["INACTIVE", "ACTIVE"]

    def test_renew_expired_starts_today(self, env):
        env.db.add_sub(1, date(2026, 9, 1), 25)
        self.make_order(env, plan_id=1, mode="renew", staff=10, term=12)
        assert verify(env).json()["valid_till"] == "2027-11-02"

    def test_renew_expired_row_written_as_expired(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 25, status="EXPIRED")
        self.make_order(env, plan_id=1, mode="renew", staff=10, term=12)
        assert verify(env).json()["valid_till"] == "2027-11-02"

    def test_upgrade_adds_staff_and_keeps_the_end_date(self, env):
        env.db.add_sub(3, date(2027, 3, 1), 12)
        self.make_order(env, plan_id=3, mode="upgrade", staff=8, term=0, amount=D("1510.00"), base=D("1280.00"), gst=D("230.00"))
        b = verify(env).json()
        assert b["valid_till"] == "2027-03-01" and b["staff_limit"] == 20 and b["mode"] == "upgrade"
        assert env.db.active()[0]["staff_limit"] == 20

    def test_plan_change_replaces_staff_and_deactivates_the_old_plan(self, env):
        env.db.add_sub(1, date(2027, 3, 1), 12)
        self.make_order(env, plan_id=2, mode="new", staff=20, term=12)
        b = verify(env).json()
        assert b["staff_limit"] == 20 and b["valid_till"] == "2027-11-02"
        assert [s["plan_id"] for s in env.db.active()] == [2]

    def test_free_trial_is_closed(self, env):
        env.db.free_trial.append({"org_id": ORG, "is_active": True, "end_date": date(2026, 12, 1)})
        self.make_order(env)
        verify(env)
        assert env.db.free_trial[0]["is_active"] is False and env.db.free_trial[0]["end_date"] == TODAY

    def test_order_from_before_the_migration_still_activates(self, env):
        self.make_order(env, mode=None, term=None, base=None, gst=None, amount=D("8496.00"))
        b = verify(env).json()
        assert b["valid_till"] == "2027-11-02" and b["mode"] == "new"          # plan_month of the plan row
        inv = env.db.invoices[0]
        assert inv["base_amount"] + inv["cgst"] + inv["sgst"] + inv["igst"] == inv["total_amount"] == D("8496.00")

    def test_legacy_paid_but_never_activated_order_is_repaired(self, env):
        # the old webhook marked orders PAID without creating a subscription
        self.make_order(env, status="PAID", activated_at=None)
        assert verify(env).json()["message"] == "Payment verified successfully" and len(env.db.subs) == 1

    def test_flagged_order_is_not_activated(self, env):
        self.make_order(env, payment_flag="AMOUNT_MISMATCH")
        r = verify(env)
        assert r.status_code == 409 and env.db.subs == []

    def test_db_error_is_a_clean_500(self, env, monkeypatch):
        self.make_order(env)

        def boom(*a, **k):
            raise RuntimeError("SELECT * FROM secret")
        monkeypatch.setattr(pay_fn, "activate_paid_order", boom)
        r = verify(env)
        assert r.status_code == 500 and "secret" not in r.text and env.db.rollbacks >= 1


class TestInvoice:
    def test_invoice_uses_the_stored_breakdown_and_is_idempotent(self, env):
        env.db.add_order(plan_id=2, mode="new", staff=20, amount=D("8602.00"), base=D("7290.00"), gst=D("1312.00"),
                         coupon_code="WELCOME10")
        verify(env)
        inv = env.db.invoices[0]
        assert inv["invoice_number"] == "INV-2026-0001" and inv["total_amount"] == D("8602.00")
        assert (inv["base_amount"], inv["cgst"], inv["sgst"], inv["igst"]) == (D("7290.00"), D("656.00"), D("656.00"), D("0.00"))
        assert inv["razorpay_payment_id"] == "pay_1" and inv["staff_count"] == 20 and inv["valid_till"] == date(2027, 11, 2)
        verify(env)
        hook(env, "payment.captured", amount=860200)
        assert len(env.db.invoices) == 1 and env.db.seq == 1

    def test_inter_state_customer_gets_igst(self, env):
        env.db.orgs[ORG]["state"] = "Karnataka"
        env.db.add_order()
        verify(env)
        inv = env.db.invoices[0]
        assert (inv["cgst"], inv["sgst"], inv["igst"]) == (D("0.00"), D("0.00"), D("1296.00"))
        email, data = env.sent[0]
        assert data["igst"] == 1296.0 and data["total_amount"] == 8496.0 and email == "asha@example.com"

    def test_email_data_and_override_email(self, env):
        env.db.add_order()
        verify(env, email="billing@example.com")
        email, data = env.sent[0]
        assert email == "billing@example.com" and data["invoice_number"] == "INV-2026-0001" and data["place_of_supply"] == "Tamil Nadu (33)"
        assert data["items"][0]["qty"] == 15 and data["items"][0]["amount"] == 7200.0

    def test_invoice_failure_does_not_undo_the_activation(self, env):
        env.db.fail_invoice_insert = True
        env.db.add_order()
        r = verify(env)
        assert r.status_code == 200 and len(env.db.subs) == 1 and env.db.invoices == [] and env.sent == []
        assert any(s.startswith("ROLLBACK TO SAVEPOINT") for s, _ in env.db.log)

    def test_invoice_numbers_are_sequential(self, env):
        env.db.add_order(oid="order_1")
        env.db.add_order(oid="order_2")
        verify(env, order_id="order_1", payment_id="pay_1")
        verify(env, order_id="order_2", payment_id="pay_2")
        assert [i["invoice_number"] for i in env.db.invoices] == ["INV-2026-0001", "INV-2026-0002"]


class TestWebhook:
    def test_raw_body_signature_is_what_counts(self, env):
        env.db.add_order()
        # non-ASCII and non-canonical whitespace: json.dumps of the parsed body would not reproduce these bytes
        raw = ('{ "event" : "payment.captured",\n "payload":{"payment":{"entity":{"id":"pay_1","order_id":"order_1",'
               '"amount":849600,"currency":"INR","notes":{"n":"Zoë ₹"}}}} }').encode("utf-8")
        assert json.dumps(json.loads(raw), separators=(",", ":")).encode() != raw
        r = hook(env, "payment.captured", raw=raw)
        assert r.status_code == 200 and env.db.orders[0]["status"] == "PAID"

    def test_wrong_missing_and_tampered_signature(self, env):
        env.db.add_order()
        assert hook(env, "payment.captured", secret="other").status_code == 400
        assert hook(env, "payment.captured", signature="deadbeef").status_code == 400
        raw = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1",
                                                                                         "amount": 849600, "currency": "INR"}}}}).encode()
        sig = sign(raw)
        r = env.client.post("/webhook/razorpay", content=raw.replace(b"849600", b"100"), headers={"X-Razorpay-Signature": sig})
        assert r.status_code == 400
        r = env.client.post("/webhook/razorpay", content=raw)           # header missing
        assert r.status_code == 400
        assert env.db.orders[0]["status"] == "CREATED" and env.db.subs == [] and env.db.events == []

    def test_unconfigured_secret_fails_closed(self, env, monkeypatch):
        monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "")
        env.db.add_order()
        raw = b'{"event":"payment.captured"}'
        r = env.client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw, "")})
        assert r.status_code == 503 and env.db.subs == []

    def test_unconfigured_secret_is_logged_and_detectable(self, env, monkeypatch, caplog):
        monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "")
        assert hook_fn.webhook_secret_configured() is False
        with caplog.at_level("ERROR"):
            assert hook(env, "payment.captured", secret="x").status_code == 503
        assert any("RAZORPAY_WEBHOOK_SECRET is not set" in r.getMessage() for r in caplog.records)
        monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
        assert hook_fn.webhook_secret_configured() is True

    def test_a_rejected_signature_leaves_a_log_line_and_stores_nothing(self, env, caplog):
        with caplog.at_level("WARNING"):
            assert hook(env, "payment.captured", secret="other", event_id="evt_bad").status_code == 400
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "signature does not match" in text and "evt_bad" in text and SECRET not in text
        assert env.db.events == []

    def test_a_delivery_without_any_event_id_is_stored_and_a_retry_is_deduplicated(self, env):
        r = hook(env, "payment.authorized", event_id=None)
        assert r.status_code == 200 and r.json() == {"success": True}
        assert len(env.db.events) == 1 and env.db.events[0]["event_id"].startswith("sha256:")
        r = hook(env, "payment.authorized", event_id=None)               # identical body: Razorpay retry
        assert r.json() == {"success": True, "duplicate": True} and len(env.db.events) == 1

    def test_msg91_events_of_one_request_are_all_stored_and_retries_are_not(self, env):
        def post(body):
            return env.client.post("/webhook/msg91/whatsapp", json=body)
        assert post({"requestId": "r1", "eventName": "sent"}).status_code == 200
        assert post({"requestId": "r1", "eventName": "delivered"}).status_code == 200
        assert post({"requestId": "r1", "eventName": "sent"}).status_code == 200      # retried delivery: no error
        assert [e["event_id"] for e in env.db.events] == ["r1:sent", "r1:delivered"]
        assert post({"status": "failed"}).status_code == 200                           # no request id: payload hash
        assert post({"status": "failed"}).status_code == 200
        assert len(env.db.events) == 3 and env.db.events[2]["event_id"].startswith("sha256:")

    def test_signature_compare_is_constant_time(self, monkeypatch):
        calls = []
        real = hmac.compare_digest
        monkeypatch.setattr(razorpay_util.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
        assert razorpay_util.verify_webhook_signature(b"x", sign(b"x"), SECRET) is True
        assert calls and razorpay_util.verify_webhook_signature(b"x", None, SECRET) is False
        assert razorpay_util.verify_webhook_signature(b"x", sign(b"x", ""), "") is False

    def test_invalid_json_with_a_good_signature(self, env):
        raw = b"not json"
        assert env.client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw)}).status_code == 400

    def test_webhook_activates_the_plan_and_invoices(self, env):
        env.db.add_order()
        r = hook(env, "payment.captured")
        assert r.status_code == 200 and r.json() == {"success": True}
        assert [s["plan_id"] for s in env.db.active()] == [2] and len(env.db.invoices) == 1 and len(env.sent) == 1
        assert env.db.payments[0]["org_id"] == ORG                         # org_id is stored (NOT NULL in the DDL)

    def test_order_paid_event_also_activates(self, env):
        env.db.add_order()
        assert hook(env, "order.paid", event_id="evt_op").status_code == 200 and len(env.db.subs) == 1

    def test_webhook_before_verify_activates_once(self, env):
        env.db.add_order()
        hook(env, "payment.captured")
        r = verify(env)
        assert r.status_code == 200 and r.json()["message"] == "Payment already verified" and r.json()["valid_till"] == "2027-11-02"
        assert len(env.db.subs) == 1 and len(env.db.payments) == 1 and len(env.db.invoices) == 1 and len(env.sent) == 1

    def test_verify_before_webhook_activates_once(self, env):
        env.db.add_order()
        verify(env)
        r = hook(env, "payment.captured")
        assert r.status_code == 200
        assert len(env.db.subs) == 1 and len(env.db.payments) == 1 and len(env.db.invoices) == 1 and len(env.sent) == 1

    def test_duplicate_event_is_ignored_with_200(self, env):
        env.db.add_order()
        hook(env, "payment.captured", event_id="evt_9")
        before = (len(env.db.subs), len(env.db.payments), len(env.db.invoices))
        r = hook(env, "payment.captured", event_id="evt_9")
        assert r.status_code == 200 and r.json() == {"success": True, "duplicate": True}
        assert (len(env.db.subs), len(env.db.payments), len(env.db.invoices)) == before

    def test_same_payment_under_a_new_event_id_is_still_a_no_op(self, env):
        env.db.add_order()
        hook(env, "payment.captured", event_id="evt_1")
        hook(env, "payment.captured", event_id="evt_2")
        hook(env, "order.paid", event_id="evt_3")
        assert len(env.db.subs) == 1 and len(env.db.invoices) == 1

    def test_unknown_order_returns_200_without_side_effects(self, env):
        r = hook(env, "payment.captured", order_id="order_ghost")
        assert r.status_code == 200 and env.db.subs == [] and env.db.payments == []

    def test_unknown_event_types_are_stored_only(self, env):
        env.db.add_order()
        r = hook(env, "refund.created", event_id="evt_r")
        assert r.status_code == 200 and env.db.orders[0]["status"] == "CREATED" and len(env.db.events) == 1 and env.db.subs == []

    def test_amount_mismatch_is_flagged_and_never_activated(self, env):
        env.db.add_order()
        r = hook(env, "payment.captured", amount=100)                          # Rs 1 instead of Rs 8,496
        assert r.status_code == 200
        assert env.db.orders[0]["payment_flag"] == "AMOUNT_MISMATCH" and env.db.orders[0]["status"] == "CREATED"
        assert env.db.subs == [] and env.db.payments == [] and env.db.invoices == []
        assert verify(env).status_code == 409                                   # nor through the client call

    def test_currency_mismatch_is_flagged(self, env):
        env.db.add_order()
        hook(env, "payment.captured", currency="USD")
        assert env.db.orders[0]["payment_flag"] == "AMOUNT_MISMATCH" and env.db.subs == []

    def test_unreadable_amount_is_a_mismatch(self, env):
        env.db.add_order()
        hook(env, "payment.captured", amount="abc")
        assert env.db.orders[0]["payment_flag"] == "AMOUNT_MISMATCH"

    def test_payment_failed_marks_failed_but_never_overwrites_paid(self, env):
        env.db.add_order()
        hook(env, "payment.failed", event_id="evt_f1")
        assert env.db.orders[0]["status"] == "FAILED"
        hook(env, "payment.captured", event_id="evt_c1")           # the retry succeeds on the same order
        assert env.db.orders[0]["status"] == "PAID" and len(env.db.subs) == 1
        hook(env, "payment.failed", event_id="evt_f2")             # a late failed attempt
        assert env.db.orders[0]["status"] == "PAID"

    def test_processing_error_answers_500_so_razorpay_retries(self, env, monkeypatch):
        env.db.add_order()

        def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(pay_fn, "activate_paid_order", boom)
        r = hook(env, "payment.captured")
        assert r.status_code == 500 and "db down" not in r.text and env.db.rollbacks >= 1

    def test_event_without_an_id_is_still_processed_once(self, env):
        env.db.add_order()
        hook(env, "payment.captured", event_id=None)
        hook(env, "payment.captured", event_id=None)
        assert len(env.db.subs) == 1 and len(env.db.invoices) == 1
