"""DB-free tests of Refer & Earn: codes, registration, rewards, pricing, activation, operator views, routes and guards.

A stateful fake database (the billing fake of tests/test_orders_billing.py plus the three referral tables) stands in for
PostgreSQL. Nothing here runs against a real database: the SQL itself is only checked for the predicates that carry the
rules (``WHERE status = 'AVAILABLE'`` ...) and by tests/test_schema_consistency.py / tests/test_sql_static_check.py.
Run: python -m pytest tests/test_referral.py -q
"""
import hashlib
import hmac
import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_function as af  # noqa: E402
from core_functions import auth_policy as P  # noqa: E402
from core_functions import gen_dashboard_functions as dash_fn  # noqa: E402
from core_functions import gen_orders_functions as orders_fn  # noqa: E402
from core_functions import gen_payment_functions as pay_fn  # noqa: E402
from core_functions import gen_webhook_functions as hook_fn  # noqa: E402
from core_functions import operator_auth, pricing  # noqa: E402
from core_functions import referral_functions as ref_fn  # noqa: E402
from router import gen_dashboard, gen_orders, gen_payments, gen_webhook, referral  # noqa: E402
from test_orders_billing import ADMIN, ORG, SECRET, TODAY, FakeConn, FakeCursor, FakeDB, FakeRazorpay, norm  # noqa: E402
from utils import rate_limit  # noqa: E402

D = Decimal
REF_ORG, REF_ADMIN = "org-ref", "user-ref"
THIRD_ORG = "org-3"


# ═════════════════════════════════════════════════════════════════════════ the fake
class RefCursor(FakeCursor):
    """FakeCursor + the referral statements. Anything else is the billing fake (or, with ``db.lenient``, a harmless no-op)."""

    def _rows(self, rows):
        self._all = list(rows)
        self._one = self._all[0] if self._all else None
        self.rowcount = len(self._all)

    def execute(self, sql, params=None):
        db = self.db
        s = norm(sql)
        p = tuple(params or ())
        handled = self._referral(db, s, p)
        if handled:
            db.log.append((s, p))
            return
        try:
            return super().execute(sql, params)
        except AssertionError:
            if not db.lenient:
                raise
            self._set(None, rowcount=1)

    # ------------------------------------------------------------------------------------------------
    def _referral(self, db, s, p):
        self._one, self._all, self.rowcount = None, [], 0
        if s == "SELECT code FROM org_referral_code WHERE org_id = %s":
            self._set({"code": db.codes[p[0]]} if p[0] in db.codes else None)
            return True
        if s.startswith("INSERT INTO org_referral_code (org_id, code) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING code"):
            if p[0] in db.codes or p[1] in db.codes.values():
                self._set(None, rowcount=0)
            else:
                db.codes[p[0]] = p[1]
                self._set({"code": p[1]}, rowcount=1)
            return True
        if s.startswith("SELECT c.org_id, o.org_name FROM org_referral_code c JOIN org_info o"):
            for org_id, code in db.codes.items():
                org = db.orgs.get(org_id)
                if code == p[0] and org and org.get("status", 1) != 2:
                    self._set({"org_id": org_id, "org_name": org["org_name"]})
                    return True
            self._set(None)
            return True
        if s.startswith("INSERT INTO referral (referrer_org_id, referred_org_id, code)"):
            if any(r["referred_org_id"] == p[1] for r in db.referrals):
                self._set(None, rowcount=0)
                return True
            row = {"referral_id": len(db.referrals) + 1, "referrer_org_id": p[0], "referred_org_id": p[1], "code": p[2],
                   "created_at": db.tick(), "qualified_at": None}
            db.referrals.append(row)
            self._set({"referral_id": row["referral_id"]}, rowcount=1)
            return True
        if s.startswith("INSERT INTO referral_reward (referral_id, org_id, role, status, percent, available_at)"):
            now = db.tick()
            db.rewards.append({"reward_id": len(db.rewards) + 1, "referral_id": p[0], "org_id": p[1], "role": p[2], "status": p[3],
                               "percent": p[4], "created_at": now, "available_at": now if s.endswith("NOW())") else None,
                               "used_at": None, "used_order_id": None, "used_invoice_number": None, "used_plan_name": None,
                               "used_discount_amount": None})
            self._set(None, rowcount=1)
            return True
        if s.startswith("SELECT r.referral_id, r.created_at, r.qualified_at, o.org_name, rw.reward_id"):
            rows = []
            for r in sorted((x for x in db.referrals if x["referrer_org_id"] == p[0]), key=lambda x: (x["created_at"], x["referral_id"]), reverse=True):
                rw = next((x for x in db.rewards if x["referral_id"] == r["referral_id"] and x["role"] == "referrer"), None) or {}
                rows.append({"referral_id": r["referral_id"], "created_at": r["created_at"], "qualified_at": r["qualified_at"],
                             "org_name": db.orgs.get(r["referred_org_id"], {}).get("org_name"),
                             "reward_id": rw.get("reward_id"), "status": rw.get("status"), "percent": rw.get("percent"),
                             "used_at": rw.get("used_at"), "used_plan_name": rw.get("used_plan_name"),
                             "used_discount_amount": rw.get("used_discount_amount")})
            self._rows(rows)
            return True
        if s.startswith("SELECT status, COUNT(*) AS cnt FROM referral_reward WHERE org_id = %s GROUP BY status"):
            counts = {}
            for r in db.rewards:
                if r["org_id"] == p[0]:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
            self._rows([{"status": k, "cnt": v} for k, v in counts.items()])
            return True
        if s.startswith("SELECT rw.reward_id, rw.status, rw.percent, rw.used_at, rw.used_plan_name, rw.used_discount_amount, ro.org_name AS referred_by_org_name"):
            for r in db.referrals:
                if r["referred_org_id"] == p[0]:
                    rw = next(x for x in db.rewards if x["referral_id"] == r["referral_id"] and x["role"] == "referred")
                    self._set({**rw, "referred_by_org_name": db.orgs[r["referrer_org_id"]]["org_name"]})
                    return True
            self._set(None)
            return True
        if s.startswith("SELECT plan_name FROM gen_plans WHERE plan_id = ANY(%s)"):
            self._rows([{"plan_name": db.plans[i]["plan_name"]} for i in sorted(p[0]) if i in db.plans])
            return True
        # operator
        if s.startswith("SELECT COUNT(*) AS total_referrals, COUNT(qualified_at) AS converted FROM referral"):
            self._set({"total_referrals": len(db.referrals), "converted": len([r for r in db.referrals if r["qualified_at"]])})
            return True
        if s.startswith("SELECT COUNT(*) AS issued,"):
            used = [r for r in db.rewards if r["status"] == "USED"]
            self._set({"issued": len(db.rewards), "available": len([r for r in db.rewards if r["status"] == "AVAILABLE"]),
                       "pending": len([r for r in db.rewards if r["status"] == "PENDING"]), "used": len(used),
                       "discount_given": sum((r["used_discount_amount"] or D(0) for r in used), D(0))})
            return True
        if s.startswith("SELECT r.referrer_org_id AS org_id, o.org_name, c.code, COUNT(*) AS referrals"):
            agg = {}
            for r in db.referrals:
                a = agg.setdefault(r["referrer_org_id"], [0, 0])
                a[0] += 1
                a[1] += 1 if r["qualified_at"] else 0
            rows = [{"org_id": k, "org_name": db.orgs[k]["org_name"], "code": db.codes.get(k), "referrals": v[0], "converted": v[1]}
                    for k, v in agg.items()]
            rows.sort(key=lambda x: (-x["converted"], -x["referrals"], x["org_name"]))
            self._rows(rows[:10])
            return True
        if s.startswith("SELECT r.referral_id, r.code, r.referrer_org_id, ro.org_name AS referrer_org_name"):
            paid = "r.qualified_at IS NOT NULL" in s
            unpaid = "r.qualified_at IS NULL" in s
            pattern = None
            if "ILIKE" in s:
                pattern = p[0].strip("%").replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\").lower()
            rows = []
            for r in sorted(db.referrals, key=lambda x: (x["created_at"], x["referral_id"]), reverse=True):
                if (paid and not r["qualified_at"]) or (unpaid and r["qualified_at"]):
                    continue
                ro, rd = db.orgs[r["referrer_org_id"]], db.orgs[r["referred_org_id"]]
                if pattern is not None:
                    hay = [ro["org_name"], rd["org_name"], r["code"], db.codes.get(r["referred_org_id"], "")]
                    if not any(pattern in (h or "").lower() for h in hay):
                        continue
                row = {"referral_id": r["referral_id"], "code": r["code"], "referrer_org_id": r["referrer_org_id"],
                       "referrer_org_name": ro["org_name"], "referred_org_id": r["referred_org_id"],
                       "referred_org_name": rd["org_name"], "created_at": r["created_at"], "qualified_at": r["qualified_at"]}
                for prefix, role in (("ra_", "referrer"), ("rb_", "referred")):
                    rw = next((x for x in db.rewards if x["referral_id"] == r["referral_id"] and x["role"] == role), None) or {}
                    row.update({prefix + "reward_id": rw.get("reward_id"), prefix + "status": rw.get("status"),
                                prefix + "used_at": rw.get("used_at"), prefix + "plan_name": rw.get("used_plan_name"),
                                prefix + "discount_amount": rw.get("used_discount_amount"),
                                prefix + "invoice_number": rw.get("used_invoice_number")})
                rows.append(row)
            self._rows(rows)
            return True
        if s.startswith("UPDATE referral_reward SET status = 'VOID'"):
            for r in db.rewards:
                if r["reward_id"] == p[0] and r["status"] in ("AVAILABLE", "PENDING"):
                    r["status"] = "VOID"
                    self._set({"reward_id": r["reward_id"]}, rowcount=1)
                    return True
            self._set(None, rowcount=0)
            return True
        if s == "SELECT status FROM referral_reward WHERE reward_id = %s":
            row = next((r for r in db.rewards if r["reward_id"] == p[0]), None)
            self._set({"status": row["status"]} if row else None)
            return True
        return False


class RefConn(FakeConn):
    def cursor(self, cursor_factory=None):
        return RefCursor(self.db)


def _extend(db):
    db.lenient = False
    db._clock = datetime(2026, 11, 1, 9, 0, 0)

    def tick():
        db._clock += timedelta(minutes=1)
        return db._clock

    db.tick = tick
    for org_id, name, parent in ((REF_ORG, "Zed Corp", REF_ADMIN), (THIRD_ORG, "Third Ltd", "user-3")):
        db.orgs[org_id] = {"org_id": org_id, "org_name": name, "parent_id": parent, "state": "Tamil Nadu"}
    db.users[REF_ADMIN] = {"user_id": REF_ADMIN, "name": "Zed", "email": "z@example.com", "mobile_number": "9000000001"}
    return db


@pytest.fixture
def env(monkeypatch):
    db = _extend(FakeDB())
    rz = FakeRazorpay()
    sent = []
    for mod in (orders_fn, pay_fn, hook_fn, ref_fn, af):
        monkeypatch.setattr(mod, "get_db_connection", lambda: RefConn(db))
        monkeypatch.setattr(mod, "return_connection", lambda c: None)
    for mod in (orders_fn, pay_fn):
        monkeypatch.setattr(mod, "get_razorpay_client", lambda: (rz, "rzp_test_key"))
        monkeypatch.setattr(mod, "ist_today", lambda: TODAY)
    monkeypatch.setattr(pay_fn, "send_invoice_email", lambda email, data: sent.append((email, data)))
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SELLER_PLACE_OF_SUPPLY", "Tamil Nadu (33)")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.delenv("OPERATOR_ENFORCE", raising=False)
    app = FastAPI()
    for r in (gen_orders.router, gen_payments.router, gen_webhook.router, referral.router, gen_dashboard.router):
        app.include_router(r)
    client = TestClient(app)
    yield type("Env", (), {"db": db, "rz": rz, "sent": sent, "client": client, "app": app})()
    operator_auth.set_lookup(None)
    rate_limit.reset()


def seed_referral(db, referrer=REF_ORG, referred=ORG):
    """Register `referred` with the referrer's code through the real function. Returns (referral_id, code)."""
    cur = RefCursor(db)
    code = ref_fn.get_or_create_code(cur, referrer)
    return ref_fn.attach_referral_at_registration(cur, referred, code), code


def reward(db, org_id, role=None, status=None):
    rows = [r for r in db.rewards if r["org_id"] == org_id and (role is None or r["role"] == role) and (status is None or r["status"] == status)]
    return rows[0] if rows else None


def add_reward(db, org_id, status="AVAILABLE", role="referred", referral_id=1):
    now = db.tick()
    row = {"reward_id": len(db.rewards) + 1, "referral_id": referral_id, "org_id": org_id, "role": role, "status": status, "percent": 10,
           "created_at": now, "available_at": now if status == "AVAILABLE" else None, "used_at": None, "used_order_id": None,
           "used_invoice_number": None, "used_plan_name": None, "used_discount_amount": None}
    db.rewards.append(row)
    return row


def quote_body(**kw):
    body = {"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "new"}
    body.update(kw)
    return body


def create_body(**kw):
    kw.setdefault("amount", 7646)
    return quote_body(**kw)


def verify(env, order_id="order_srv_1", payment_id="pay_1"):
    return env.client.post("/payments/verify", json={"user_id": ADMIN, "razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
                                                     "razorpay_signature": "good-signature"})


def webhook(env, order_id, payment_id, amount, event_id):
    payload = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": amount,
                                                                              "currency": "INR", "status": "captured"}}}}
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return env.client.post("/webhook/razorpay", content=raw, headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id,
                                                                      "Content-Type": "application/json"})


# ═════════════════════════════════════════════════════════════════════════ code generation
class TestCodes:
    def test_format_of_generated_codes(self):
        seen = set()
        for _ in range(2000):
            code = ref_fn.generate_code()
            seen.add(code)
            assert len(code) == 6
            assert any(c.isalpha() for c in code) and any(c.isdigit() for c in code)
            assert code == code.upper() and re.fullmatch(r"[A-Z0-9]{6}", code)
            assert not set(code) & set("0O1IL"), code
            assert ref_fn.is_valid_code_format(code)
        assert len(seen) > 1900                                            # random, not a constant

    @pytest.mark.parametrize("code", ["ABCDEF", "234567", "ABCD0F", "ABCD1F", "ABCDIF", "ABCDLF", "ABCDOF", "ABC12", "ABC1234", "", None, "AB C23", "ABCD-2"])
    def test_malformed_codes_are_rejected(self, code):
        assert ref_fn.is_valid_code_format(code) is False

    def test_a_lowercase_or_padded_code_is_accepted_after_normalising(self):
        assert ref_fn.is_valid_code_format(" k7m2qx ") is True

    def test_get_or_create_is_stable_and_stored_once(self, env):
        cur = RefCursor(env.db)
        first = ref_fn.get_or_create_code(cur, ORG)
        assert ref_fn.get_or_create_code(cur, ORG) == first == env.db.codes[ORG]
        assert len(env.db.codes) == 1

    def test_collision_retries_with_a_fresh_code(self, env, monkeypatch):
        env.db.codes["someone-else"] = "AAAA22"
        candidates = iter(["AAAA22", "AAAA22", "BBBB33"])
        monkeypatch.setattr(ref_fn, "generate_code", lambda rng=None: next(candidates))
        cur = RefCursor(env.db)
        assert ref_fn.get_or_create_code(cur, ORG) == "BBBB33"
        inserts = [x for x in env.db.log if x[0].startswith("INSERT INTO org_referral_code")]
        assert len(inserts) == 3 and env.db.codes[ORG] == "BBBB33" and env.db.codes["someone-else"] == "AAAA22"

    def test_giving_up_is_a_clean_503(self, env, monkeypatch):
        env.db.codes["someone-else"] = "AAAA22"
        monkeypatch.setattr(ref_fn, "generate_code", lambda rng=None: "AAAA22")
        with pytest.raises(HTTPException) as e:
            ref_fn.get_or_create_code(RefCursor(env.db), ORG)
        assert e.value.status_code == 503

    def test_unique_constraint_is_the_final_guard_sql(self, env):
        ref_fn.get_or_create_code(RefCursor(env.db), ORG)
        sql = [x[0] for x in env.db.log if x[0].startswith("INSERT INTO org_referral_code")][0]
        assert "ON CONFLICT DO NOTHING" in sql                                # both unique keys (org_id PK, code UNIQUE)


# ═════════════════════════════════════════════════════════════════════════ registration
def reg_request(**kw):
    base = dict(name="Asha", emp_id="E1", mobile_number="9111111111", email="new@example.com", org_name="New Org",
                org_address="Road", branches=["HQ"])
    base.update(kw)
    return af.AdminRegisterRequest(**base)


@pytest.fixture
def reg(env, monkeypatch):
    import ml.s3
    env.db.lenient = True
    monkeypatch.setattr(ml.s3, "get_org_logo_url", lambda org_id: None)
    monkeypatch.setattr(af._auth, "issue_for_user", lambda cursor, user_id: {})
    created = {}
    orig = RefCursor.execute

    def spy(self, sql, params=None):
        s = norm(sql)
        if s.startswith("INSERT INTO org_info"):
            created["org_id"] = params[0]
            self.db.orgs[params[0]] = {"org_id": params[0], "org_name": params[1], "parent_id": params[4]}
        if s.startswith("SELECT * FROM users WHERE mobile_number"):
            self._set(None)
            return
        return orig(self, sql, params)

    monkeypatch.setattr(RefCursor, "execute", spy)
    env.created = created
    return env


def register(env, **kw):
    import asyncio
    return asyncio.run(af.admin_register_json(reg_request(**kw)))


class TestRegistration:
    def test_request_models_accept_referral_code(self):
        from router import auth as auth_router
        assert "referral_code" in af.AdminRegisterRequest.model_fields and "referral_code" in auth_router.AdminRegisterRequest.model_fields
        assert af.AdminRegisterRequest.model_fields["referral_code"].default is None
        assert auth_router.AdminRegisterRequest.model_fields["referral_code"].default is None

    def test_valid_code_creates_the_referral_and_both_rewards(self, reg):
        code = ref_fn.get_or_create_code(RefCursor(reg.db), REF_ORG)
        out = register(reg, referral_code=code.lower())                          # case does not matter
        new_org = out["org_id"]
        assert len(reg.db.referrals) == 1
        r = reg.db.referrals[0]
        assert (r["referrer_org_id"], r["referred_org_id"], r["code"], r["qualified_at"]) == (REF_ORG, new_org, code, None)
        referred, referrer = reward(reg.db, new_org), reward(reg.db, REF_ORG)
        assert (referred["role"], referred["status"], referred["percent"]) == ("referred", "AVAILABLE", 10) and referred["available_at"]
        assert (referrer["role"], referrer["status"], referrer["percent"]) == ("referrer", "PENDING", 10) and referrer["available_at"] is None
        assert reg.db.commits >= 1 and reg.db.codes[new_org]                     # the new organisation also got its own code

    def test_no_code_creates_no_referral(self, reg):
        out = register(reg)
        assert reg.db.referrals == [] and reg.db.rewards == [] and out["success"] is True
        assert register_blank(reg).get("success") is True and reg.db.referrals == []

    def test_unknown_code_is_a_400_before_the_organisation_exists(self, reg):
        with pytest.raises(HTTPException) as e:
            register(reg, referral_code="ZZZZ99")
        assert e.value.status_code == 400 and e.value.detail == "This referral code is not valid"
        assert "org_id" not in reg.created                                       # nothing was inserted
        assert not any(x[0].startswith(("INSERT INTO org_info", "INSERT INTO users")) for x in reg.db.log)
        assert reg.db.commits == 0 and reg.db.referrals == [] and reg.db.rewards == []

    @pytest.mark.parametrize("bad", ["ABC", "0000000", "abc 123", "AAAAAAA"])
    def test_malformed_code_is_a_400(self, reg, bad):
        with pytest.raises(HTTPException) as e:
            register(reg, referral_code=bad)
        assert e.value.status_code == 400 and "org_id" not in reg.created

    def test_own_code_is_refused(self, env):
        code = ref_fn.get_or_create_code(RefCursor(env.db), ORG)
        with pytest.raises(HTTPException) as e:
            ref_fn.attach_referral_at_registration(RefCursor(env.db), ORG, code)
        assert e.value.status_code == 400 and "own" in e.value.detail
        assert env.db.referrals == [] and env.db.rewards == []

    def test_an_organisation_can_be_referred_only_once(self, env):
        seed_referral(env.db)
        third_code = ref_fn.get_or_create_code(RefCursor(env.db), THIRD_ORG)
        with pytest.raises(HTTPException) as e:
            ref_fn.attach_referral_at_registration(RefCursor(env.db), ORG, third_code)
        assert e.value.status_code == 400
        assert len(env.db.referrals) == 1 and len(env.db.rewards) == 2           # nothing half written

    def test_reward_states_after_attach(self, env):
        rid, code = seed_referral(env.db)
        assert rid == 1
        referred, referrer = reward(env.db, ORG), reward(env.db, REF_ORG)
        assert (referred["status"], referred["role"]) == ("AVAILABLE", "referred")
        assert (referrer["status"], referrer["role"]) == ("PENDING", "referrer")

    def test_a_failure_while_attaching_rolls_the_registration_back(self, reg, monkeypatch):
        code = ref_fn.get_or_create_code(RefCursor(reg.db), REF_ORG)

        def boom(cursor, org_id, code):
            raise RuntimeError("db down")
        monkeypatch.setattr(ref_fn, "attach_referral_at_registration", boom)
        with pytest.raises(HTTPException) as e:
            register(reg, referral_code=code)
        assert e.value.status_code == 500 and reg.db.commits == 0 and reg.db.rollbacks == 1

    def test_the_referral_is_written_before_the_first_commit(self, reg, monkeypatch):
        code = ref_fn.get_or_create_code(RefCursor(reg.db), REF_ORG)
        sequence = []
        real_commit, real_execute = FakeConn.commit, RefCursor.execute

        def commit(self):
            sequence.append("commit")
            return real_commit(self)

        def execute(self, sql, params=None):
            s = norm(sql)
            if s.startswith(("INSERT INTO org_info", "INSERT INTO referral ", "INSERT INTO referral_reward")):
                sequence.append(s.split(" (")[0])
            return real_execute(self, sql, params)
        monkeypatch.setattr(FakeConn, "commit", commit)
        monkeypatch.setattr(RefCursor, "execute", execute)
        register(reg, referral_code=code)
        assert sequence[:4] == ["INSERT INTO org_info", "INSERT INTO referral", "INSERT INTO referral_reward", "INSERT INTO referral_reward"]
        assert sequence[4] == "commit"                                           # org + referral + both rewards share one transaction


def register_blank(env):
    return register(env, mobile_number="9222222222", email="b@example.com", referral_code="   ")


# ═════════════════════════════════════════════════════════════════════════ pure pricing
PRIME = pricing.PlanTier(2, "Prime", D("40.00"), 12)
ELITE = pricing.PlanTier(3, "Elite", D("40.00"), 12)
CORE = pricing.PlanTier(1, "Core", D("25.00"), 6)
OFFER = pricing.ReferralOffer(available_count=1, reward_id=7)


def current(tier=CORE, end=date(2027, 3, 1), staff=12, status="ACTIVE"):
    return pricing.CurrentSubscription(plan=tier, status=status, start_date=date(2026, 3, 1), end_date=end, staff_limit=staff)


def bq(mode="new", target=PRIME, staff=15, cur=None, **kw):
    return pricing.build_quote(mode=mode, target=target, staff=staff, current=cur, today=TODAY, **kw)


class TestPricing:
    def test_constants_live_in_one_place(self):
        assert pricing.REFERRAL_PERCENT == 10 and pricing.REFERRAL_ELIGIBLE_PLAN_IDS == (2, 3)
        assert ref_fn.REFERRAL_PERCENT == 10 and ref_fn.REFERRAL_ELIGIBLE_PLAN_IDS is pricing.REFERRAL_ELIGIBLE_PLAN_IDS

    def test_percent_math_without_credit(self):
        q = bq(referral=OFFER, apply_referral=True)
        assert (q.staff_charge, q.after_credit, q.discount_amount, q.subtotal, q.gst, q.grand_total) == (
            D("7200.00"), D("7200.00"), D("720.00"), D("6480.00"), D("1166.00"), D("7646.00"))
        assert q.referral_applied and q.referral_reward_id == 7 and not q.coupon_applied and q.coupon_code is None
        assert q.discount_percent == 10

    def test_percent_is_taken_after_the_plan_credit(self):
        q = bq(cur=current(), referral=OFFER, apply_referral=True)               # credit = 10 x 25 x 6 = 1500
        assert (q.credit, q.after_credit, q.discount_amount, q.subtotal, q.gst, q.grand_total) == (
            D("1500.00"), D("5700.00"), D("570.00"), D("5130.00"), D("923.00"), D("6053.00"))

    def test_same_total_as_an_equivalent_percent_coupon(self):
        coupon = pricing.Coupon(name="TEN", discount=D("10"))
        a = bq(cur=current(), coupon=coupon, coupon_code="TEN")
        b = bq(cur=current(), referral=OFFER, apply_referral=True)
        assert (a.discount_amount, a.subtotal, a.gst, a.grand_total) == (b.discount_amount, b.subtotal, b.gst, b.grand_total)

    def test_whole_rupee_half_up_rounding(self):
        tier = pricing.PlanTier(2, "Prime", D("60.0417"), 12)                     # 10 x 60.0417 x 12 = 7205.004 -> 7205.00
        q = bq(target=tier, staff=10, referral=OFFER, apply_referral=True)
        assert q.after_credit == D("7205.00") and q.discount_amount == D("721.00")   # 720.5 -> 721 (half-even would give 720)
        assert q.subtotal == D("6484.00") and q.gst == D("1167.00") and q.grand_total == D("7651.00")

    def test_elite_and_renewal_are_eligible(self):
        assert bq(target=ELITE, referral=OFFER, apply_referral=True).referral_applied
        q = bq("renew", target=None, staff=10, cur=current(PRIME, staff=10), referral=OFFER, apply_referral=True)
        assert q.referral_applied and q.discount_amount == D("480.00") and q.grand_total == D("5098.00")   # 4800 - 480 = 4320 + 778

    def test_core_is_rejected(self):
        with pytest.raises(pricing.PricingError) as e:
            bq(target=CORE, staff=10, referral=OFFER, apply_referral=True)
        assert e.value.code == "referral_not_applicable" and e.value.status == 400
        assert e.value.message == "Referral rewards are valid on Prime and Elite plans only"
        q = bq(target=CORE, staff=10, referral=OFFER, apply_referral=True, strict_referral=False)
        assert not q.referral_applied and q.referral_message == "Referral rewards are valid on Prime and Elite plans only"
        assert q.discount_amount == 0 and q.grand_total == D("1770.00")          # 10 x 25 x 6 = 1500 + 270
        assert q.referral_eligible is False and q.referral_available_count == 1

    def test_renewing_core_is_rejected_too(self):
        q = bq("renew", target=None, staff=10, cur=current(CORE), referral=OFFER, apply_referral=True, strict_referral=False)
        assert not q.referral_applied and q.referral_message == "Referral rewards are valid on Prime and Elite plans only"

    def test_upgrade_is_rejected(self):
        cur = current(PRIME, end=date(2027, 3, 1), staff=12)
        with pytest.raises(pricing.PricingError) as e:
            bq("upgrade", target=None, staff=5, cur=cur, referral=OFFER, apply_referral=True)
        assert e.value.code == "referral_not_applicable" and e.value.message == "Referral rewards do not apply to staff upgrades"
        q = bq("upgrade", target=None, staff=5, cur=cur, referral=OFFER, apply_referral=True, strict_referral=False)
        assert not q.referral_applied and q.referral_message == "Referral rewards do not apply to staff upgrades" and q.referral_eligible is False

    def test_coupon_and_referral_together_are_rejected_strict_or_not(self):
        coupon = pricing.Coupon(name="TEN", discount=D("10"))
        for strict in (True, False):
            with pytest.raises(pricing.PricingError) as e:
                bq(coupon=coupon, coupon_code="TEN", referral=OFFER, apply_referral=True, strict_referral=strict, strict_coupon=strict)
            assert e.value.code == "referral_with_coupon" and e.value.status == 400
            assert e.value.message == "Use either a coupon or a referral reward, not both"

    def test_a_coupon_without_the_referral_request_is_unchanged(self):
        q = bq(coupon=pricing.Coupon(name="TEN", discount=D("10")), coupon_code="TEN", referral=OFFER)
        assert q.coupon_applied and not q.referral_applied and q.grand_total == D("7646.00") and q.referral_eligible is True

    def test_no_reward_available(self):
        none = pricing.ReferralOffer()
        q = bq(referral=none, apply_referral=True, strict_referral=False)
        assert not q.referral_applied and q.referral_message == "No referral reward available" and q.referral_available_count == 0
        assert q.referral_eligible is False and q.referral_eligible_message == "No referral reward available"
        with pytest.raises(pricing.PricingError) as e:
            bq(referral=none, apply_referral=True)
        assert e.value.code == "referral_not_applicable" and e.value.message == "No referral reward available"
        assert bq().referral_available_count == 0                               # referral=None behaves like no reward

    def test_the_quote_always_reports_the_referral_block(self):
        d = bq(referral=pricing.ReferralOffer(available_count=2, reward_id=7)).to_dict()           # not requested
        assert d["referral_applied"] is False and d["referral_percent"] == 10 and d["referral_message"] is None
        assert d["referral"] == {"available_count": 2, "eligible": True, "percent": 10, "message": None}
        assert d["grand_total"] == 8496.0                                                         # untouched
        assert "referral_reward_id" not in d and "referral_reward_id" not in d["referral"]          # the reward id stays server-side
        d = bq(target=CORE, staff=10, referral=OFFER).to_dict()
        assert d["referral"] == {"available_count": 1, "eligible": False, "percent": 10,
                                 "message": "Referral rewards are valid on Prime and Elite plans only"}
        assert bq(referral=OFFER, apply_referral=True).to_dict()["referral_applied"] is True
        json.dumps(d)

    def test_pick_mode_still_works_with_a_referral_total(self):
        cur = current(PRIME, end=date(2027, 3, 1), staff=12)
        mode = pricing.pick_mode(target=PRIME, staff=10, current=cur, today=TODAY, client_amount=5098, referral=OFFER, apply_referral=True)
        assert mode == "renew"


# ═════════════════════════════════════════════════════════════════════════ checkout over HTTP
class TestCheckout:
    def test_quote_reports_referral_even_when_not_applied(self, env):
        seed_referral(env.db)
        q = env.client.post("/orders/quote", json=quote_body()).json()["data"]
        assert q["referral"] == {"available_count": 1, "eligible": True, "percent": 10, "message": None}
        assert q["referral_applied"] is False and q["grand_total"] == 8496.0

    def test_quote_with_a_reward(self, env):
        seed_referral(env.db)
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral_applied"] is True and q["discount_amount"] == 720.0 and q["coupon_applied"] is False
        assert q["grand_total"] == 7646.0 and q["referral_message"] is None
        assert env.db.orders == []                                               # a quote stores nothing

    def test_quote_reports_an_unusable_request_without_failing(self, env):
        seed_referral(env.db)
        r = env.client.post("/orders/quote", json=quote_body(plan_id=1, apply_referral=True))
        q = r.json()["data"]
        assert r.status_code == 200 and q["referral_applied"] is False
        assert q["referral_message"] == "Referral rewards are valid on Prime and Elite plans only"
        assert q["referral"]["eligible"] is False

    def test_quote_without_any_reward(self, env):
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral"] == {"available_count": 0, "eligible": False, "percent": 10, "message": "No referral reward available"}
        assert q["referral_message"] == "No referral reward available" and q["grand_total"] == 8496.0

    def test_quote_with_coupon_and_referral_is_a_400(self, env):
        seed_referral(env.db)
        r = env.client.post("/orders/quote", json=quote_body(apply_referral=True, coupon_code="SAVE10"))
        assert r.status_code == 400 and r.json()["detail"] == "Use either a coupon or a referral reward, not both"
        assert r.headers["X-Error-Code"] == "referral_with_coupon"

    def test_create_stores_the_reward_and_the_discount(self, env):
        _, _ = seed_referral(env.db)
        r = env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert r.status_code == 200 and r.json()["amount"] == 7646.0 and env.rz.created[0]["amount"] == D("7646.00")
        order = env.db.orders[0]
        assert order["referral_reward_id"] == reward(env.db, ORG)["reward_id"] and order["discount_amount"] == D("720.00")
        assert order["coupon_code"] is None and r.json()["quote"]["referral_applied"] is True
        assert reward(env.db, ORG)["status"] == "AVAILABLE"                       # NOT consumed by creating the order

    def test_create_without_the_flag_does_not_use_the_reward(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(amount=8496))
        assert env.db.orders[0]["referral_reward_id"] is None and env.db.orders[0]["discount_amount"] == D("0.00")

    def test_create_refuses_what_a_quote_only_reports(self, env):
        seed_referral(env.db)
        r = env.client.post("/orders/create", json=create_body(plan_id=1, staff_limit=10, apply_referral=True))
        assert r.status_code == 400 and r.json()["detail"] == "Referral rewards are valid on Prime and Elite plans only"
        assert r.headers["X-Error-Code"] == "referral_not_applicable"
        r = env.client.post("/orders/create", json=create_body(apply_referral=True, coupon_code="SAVE10"))
        assert r.status_code == 400 and r.headers["X-Error-Code"] == "referral_with_coupon"
        env.db.orders.clear()
        env.db.rewards.clear()
        r = env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert r.status_code == 400 and r.json()["detail"] == "No referral reward available" and env.db.orders == []
        assert env.rz.created == []                                              # no Razorpay order for a refused request

    def test_upgrade_with_a_reward_is_refused(self, env):
        seed_referral(env.db)
        env.db.add_sub(3, date(2027, 3, 1), 12)
        r = env.client.post("/orders/create", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 3, "staff_limit": 8, "mode": "upgrade", "apply_referral": True})
        assert r.status_code == 400 and r.json()["detail"] == "Referral rewards do not apply to staff upgrades"

    def test_the_client_cannot_choose_the_reward(self, env):
        seed_referral(env.db)
        other = add_reward(env.db, THIRD_ORG)                                    # another organisation's reward
        body = create_body(apply_referral=True, referral_reward_id=other["reward_id"], reward_id=other["reward_id"])
        assert env.client.post("/orders/create", json=body).status_code == 200
        assert env.db.orders[0]["referral_reward_id"] == reward(env.db, ORG)["reward_id"] != other["reward_id"]

    def test_only_the_callers_own_rewards_count(self, env):
        add_reward(env.db, THIRD_ORG)
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True)).json()["data"]
        assert q["referral"]["available_count"] == 0 and q["referral_applied"] is False

    def test_pending_used_and_void_rewards_do_not_count(self, env):
        for status in ("PENDING", "USED", "VOID"):
            add_reward(env.db, ORG, status=status, role="referrer")
        assert env.client.post("/orders/quote", json=quote_body()).json()["data"]["referral"]["available_count"] == 0

    def test_the_oldest_available_reward_is_picked(self, env):
        newer = add_reward(env.db, ORG)
        older = {**add_reward(env.db, ORG)}
        env.db.rewards[-1]["created_at"] = env.db.rewards[-1]["available_at"] = datetime(2026, 1, 1)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert env.db.orders[0]["referral_reward_id"] == older["reward_id"] != newer["reward_id"]


# ═════════════════════════════════════════════════════════════════════════ activation
def pay(env, order_no=1, payment_no=1):
    return verify(env, f"order_srv_{order_no}", f"pay_{payment_no}")


class TestActivation:
    def test_verify_consumes_exactly_one_reward_and_writes_the_trail(self, env):
        seed_referral(env.db)
        add_reward(env.db, ORG, role="referrer", referral_id=99)                  # a second AVAILABLE reward of the same org
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert pay(env).status_code == 200
        used = [r for r in env.db.rewards if r["status"] == "USED"]
        assert len(used) == 1 and used[0]["reward_id"] == env.db.orders[0]["referral_reward_id"]
        assert (used[0]["used_order_id"], used[0]["used_plan_name"], used[0]["used_discount_amount"]) == (env.db.orders[0]["id"], "Prime", D("720.00"))
        assert used[0]["used_at"] is not None and used[0]["used_invoice_number"] == env.db.invoices[0]["invoice_number"]
        assert env.db.invoices[0]["referral_reward_id"] == used[0]["reward_id"]
        assert len([r for r in env.db.rewards if r["status"] == "AVAILABLE" and r["org_id"] == ORG]) == 1   # the other one is untouched
        assert env.db.orders[0]["payment_flag"] is None

    def test_activation_is_idempotent_verify_twice_and_webhook(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert pay(env).status_code == 200
        snapshot = [dict(r) for r in env.db.rewards]
        assert pay(env).status_code == 200
        assert webhook(env, "order_srv_1", "pay_1", 764600, "evt_1").status_code == 200
        assert webhook(env, "order_srv_1", "pay_1", 764600, "evt_1").status_code == 200
        assert [dict(r) for r in env.db.rewards] == snapshot
        assert len(env.db.invoices) == 1 and len(env.db.active()) == 1

    def test_the_webhook_path_consumes_too(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert webhook(env, "order_srv_1", "pay_1", 764600, "evt_1").status_code == 200
        assert reward(env.db, ORG)["status"] == "USED" and env.db.invoices[0]["referral_reward_id"] == reward(env.db, ORG)["reward_id"]

    def test_an_order_without_a_reward_consumes_nothing(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(amount=8496))
        pay(env)
        assert reward(env.db, ORG)["status"] == "AVAILABLE" and env.db.invoices[0]["referral_reward_id"] is None

    def test_the_consume_update_is_guarded_by_status_and_org(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        pay(env)
        sql = [x[0] for x in env.db.log if x[0].startswith("UPDATE referral_reward SET status = 'USED'")][0]
        assert "WHERE reward_id = %s AND org_id = %s AND status = 'AVAILABLE'" in sql

    def test_referrer_reward_flips_only_on_the_referred_orgs_first_paid_activation(self, env):
        seed_referral(env.db)
        referrer = reward(env.db, REF_ORG)
        assert referrer["status"] == "PENDING"
        # no payment yet: still pending
        assert reward(env.db, REF_ORG)["status"] == "PENDING" and env.db.referrals[0]["qualified_at"] is None
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        assert pay(env, 1, 1).status_code == 200
        ref = env.db.referrals[0]
        assert ref["qualified_at"] is not None
        assert (referrer["status"], referrer["available_at"] is not None) == ("AVAILABLE", True)
        stamp, available_at = ref["qualified_at"], referrer["available_at"]
        # re-delivered webhook / verify: nothing changes again
        webhook(env, "order_srv_1", "pay_1", 764600, "evt_9")
        pay(env, 1, 1)
        assert (ref["qualified_at"], referrer["available_at"]) == (stamp, available_at)
        # a SECOND paid order of the same org does not qualify (or re-flip) anything
        referrer["status"] = "PENDING"                                            # prove that nothing sets it again
        referrer["available_at"] = None
        env.client.post("/orders/create", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 10, "mode": "renew", "amount": 4720})
        assert pay(env, 2, 2).status_code == 200
        assert (ref["qualified_at"], referrer["status"], referrer["available_at"]) == (stamp, "PENDING", None)

    def test_a_non_referred_org_paying_does_not_touch_other_rewards(self, env):
        seed_referral(env.db, referrer=REF_ORG, referred=THIRD_ORG)
        env.client.post("/orders/create", json=create_body(amount=8496))
        pay(env)
        assert env.db.referrals[0]["qualified_at"] is None and reward(env.db, REF_ORG)["status"] == "PENDING"

    def test_qualification_needs_a_paid_activation_not_a_created_order(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(amount=8496))
        assert env.db.referrals[0]["qualified_at"] is None and reward(env.db, REF_ORG)["status"] == "PENDING"
        verify(env, "order_srv_1", "pay_1").status_code
        assert reward(env.db, REF_ORG)["status"] == "AVAILABLE"

    def test_a_voided_referrer_reward_is_not_revived(self, env):
        seed_referral(env.db)
        reward(env.db, REF_ORG)["status"] = "VOID"
        env.client.post("/orders/create", json=create_body(amount=8496))
        pay(env)
        assert reward(env.db, REF_ORG)["status"] == "VOID" and env.db.referrals[0]["qualified_at"] is not None

    def test_concurrent_orders_on_one_reward_honour_both_payments_and_flag_the_second(self, env, caplog):
        seed_referral(env.db)
        assert env.client.post("/orders/create", json=create_body(apply_referral=True)).status_code == 200      # order_srv_1
        assert env.client.post("/orders/create", json=create_body(apply_referral=True)).status_code == 200      # order_srv_2: same reward
        assert env.db.orders[0]["referral_reward_id"] == env.db.orders[1]["referral_reward_id"]
        assert pay(env, 1, 1).status_code == 200
        with caplog.at_level("WARNING"):
            r = pay(env, 2, 2)
        assert r.status_code == 200 and r.json()["success"] is True              # the payment is honoured
        assert env.db.orders[1]["status"] == "PAID" and env.db.orders[1]["payment_flag"] == "REFERRAL_ALREADY_USED"
        assert env.db.orders[0]["payment_flag"] is None
        assert any("already used" in m for m in caplog.messages)
        assert len(env.db.active()) == 1 and len(env.db.invoices) == 2           # both orders activated / invoiced
        used = [r for r in env.db.rewards if r["status"] == "USED"]
        assert len(used) == 1 and used[0]["used_order_id"] == env.db.orders[0]["id"]
        assert env.db.invoices[0]["referral_reward_id"] == used[0]["reward_id"] and env.db.invoices[1]["referral_reward_id"] is None
        # the flag is a note, not a block: verify / webhook for the flagged order stay successful no-ops
        assert pay(env, 2, 2).status_code == 200 and webhook(env, "order_srv_2", "pay_2", 764600, "evt_2").status_code == 200
        assert len(env.db.invoices) == 2

    def test_an_amount_mismatch_flag_still_blocks(self, env):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        env.db.orders[0]["payment_flag"] = "AMOUNT_MISMATCH"
        assert pay(env).status_code == 409 and reward(env.db, ORG)["status"] == "AVAILABLE"

    def test_a_referral_database_error_never_undoes_the_payment(self, env, monkeypatch, caplog):
        seed_referral(env.db)
        env.client.post("/orders/create", json=create_body(apply_referral=True))

        def boom(cursor, org_id):
            raise RuntimeError("referral table missing")
        monkeypatch.setattr(ref_fn, "qualify_referral_on_first_payment", boom)
        assert pay(env).status_code == 200
        assert len(env.db.active()) == 1 and env.db.orders[0]["status"] == "PAID"
        assert any("ROLLBACK TO SAVEPOINT referral_qual_sp" in x[0] for x in env.db.log)

    def test_the_second_reward_stays_usable_after_the_first_was_used(self, env):
        seed_referral(env.db)                                                     # ORG: reward 1 (AVAILABLE)
        second = add_reward(env.db, ORG, role="referrer", referral_id=99)
        q = env.client.post("/orders/quote", json=quote_body()).json()["data"]
        assert q["referral"]["available_count"] == 2
        env.client.post("/orders/create", json=create_body(apply_referral=True))
        pay(env, 1, 1)
        q = env.client.post("/orders/quote", json=quote_body(apply_referral=True, mode="renew", plan_id=None, staff_limit=15)).json()["data"]
        assert q["referral"]["available_count"] == 1 and q["referral"]["eligible"] is True and q["referral_applied"] is True
        env.client.post("/orders/create", json={"user_id": ADMIN, "org_id": ORG, "plan_id": 2, "staff_limit": 15, "mode": "renew", "apply_referral": True})
        assert env.db.orders[1]["referral_reward_id"] == second["reward_id"]
        pay(env, 2, 2)
        assert [r["status"] for r in env.db.rewards if r["org_id"] == ORG] == ["USED", "USED"]
        q = env.client.post("/orders/quote", json=quote_body(mode="renew", plan_id=None, staff_limit=15, apply_referral=True)).json()["data"]
        assert q["referral"]["available_count"] == 0 and q["referral_applied"] is False


# ═════════════════════════════════════════════════════════════════════════ org-admin page and validate
class TestRoutes:
    def test_validate_a_known_code_reveals_only_the_referrer_name(self, env):
        code = ref_fn.get_or_create_code(RefCursor(env.db), REF_ORG)
        r = env.client.get("/referral/validate", params={"code": code.lower()})
        assert r.status_code == 200 and r.json() == {"valid": True, "referrer_org_name": "Zed Corp"}

    @pytest.mark.parametrize("code", ["ZZZZ99", "abc", "0O1IL0", "ABCDEF", ""])
    def test_validate_an_unknown_or_malformed_code(self, env, code):
        r = env.client.get("/referral/validate", params={"code": code})
        assert r.status_code == 200 and r.json() == {"valid": False, "message": "This referral code is not valid"}

    def test_validate_does_not_confuse_itself_with_an_org_id(self, env):
        r = env.client.get("/referral/validate", params={"code": "ABCD23"})
        assert "code" not in r.json() and r.json()["valid"] is False

    def test_validate_is_rate_limited_per_ip(self, env, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("RATE_LIMIT_REFERRAL_VALIDATE", "2/60")
        rate_limit.reset()
        assert env.client.get("/referral/validate", params={"code": "ABCD23"}).status_code == 200
        assert env.client.get("/referral/validate", params={"code": "ABCD23"}).status_code == 200
        r = env.client.get("/referral/validate", params={"code": "ABCD23"})
        assert r.status_code == 429 and "Retry-After" in r.headers
        assert rate_limit.rule("REFERRAL_VALIDATE") == rate_limit.Rule("REFERRAL_VALIDATE", 2, 60)
        monkeypatch.delenv("RATE_LIMIT_REFERRAL_VALIDATE")
        assert rate_limit.rule("REFERRAL_VALIDATE") == rate_limit.Rule("REFERRAL_VALIDATE", 20, 60)

    def test_org_page_creates_the_code_lazily_and_has_the_contract_shape(self, env):
        r = env.client.get(f"/referral/{ORG}", params={"user_id": ADMIN})
        assert r.status_code == 200
        body = r.json()
        assert env.db.codes[ORG] == body["code"] and re.fullmatch(r"[A-Z0-9]{6}", body["code"])
        assert (body["percent"], body["eligible_plans"], body["referrals"], body["my_reward"]) == (10, ["Prime", "Elite"], [], None)
        assert body["summary"] == {"referred_count": 0, "converted_count": 0, "rewards_available": 0, "rewards_pending": 0, "rewards_used": 0}
        assert body["code"] in body["share_message"] and "10%" in body["share_message"]
        assert env.client.get(f"/referral/{ORG}").json()["code"] == body["code"]            # permanent
        assert env.db.commits >= 1

    def test_org_page_with_referrals_and_my_reward(self, env):
        seed_referral(env.db, referrer=REF_ORG, referred=ORG)                     # ORG was referred by Zed Corp
        seed_referral(env.db, referrer=ORG, referred=THIRD_ORG)                   # and referred Third Ltd
        add_reward(env.db, ORG, role="referrer", referral_id=1)
        r = env.client.get(f"/referral/{ORG}").json()
        assert r["my_reward"] == {"reward_id": 1, "status": "available", "percent": 10, "referred_by_org_name": "Zed Corp",
                                  "used_at": None, "plan_name": None, "discount_amount": None}
        assert len(r["referrals"]) == 1
        item = r["referrals"][0]
        assert (item["org_name"], item["status"]) == ("Third Ltd", "signed_up") and item["joined_at"].endswith("Z")
        assert item["reward"] == {"reward_id": 4, "status": "pending", "percent": 10, "used_at": None, "plan_name": None, "discount_amount": None}
        assert r["summary"] == {"referred_count": 1, "converted_count": 0, "rewards_available": 2, "rewards_pending": 1, "rewards_used": 0}
        env.db.referrals[1]["qualified_at"] = datetime(2026, 11, 3)
        assert env.client.get(f"/referral/{ORG}").json()["referrals"][0]["status"] == "paid"

    def test_org_page_unknown_org_and_foreign_user(self, env):
        assert env.client.get("/referral/nope").status_code == 404
        assert env.client.get(f"/referral/{ORG}", params={"user_id": "someone-else"}).status_code == 403
        assert env.client.get(f"/referral/{ORG}", params={"user_id": REF_ADMIN}).status_code == 403

    def test_org_page_enforces_the_token_identity(self, env):
        from types import SimpleNamespace

        def with_identity(role, org, allowed=None):
            app = FastAPI()

            @app.middleware("http")
            async def attach(request: Request, call_next):
                request.state.identity = SimpleNamespace(user_id="u", org_id=org, role_id=role)
                request.state.allowed_orgs = frozenset(allowed or {org})
                return await call_next(request)
            app.include_router(referral.router)
            return TestClient(app)
        assert with_identity(1, ORG).get(f"/referral/{ORG}").status_code == 200
        assert with_identity(1, REF_ORG).get(f"/referral/{ORG}").status_code == 403         # another organisation's page
        assert with_identity(2, ORG).get(f"/referral/{ORG}").status_code == 403             # a manager
        assert with_identity(3, ORG).get(f"/referral/{ORG}").status_code == 403             # staff

    def test_policy_classification(self):
        assert P.classify("GET", "/referral/validate") == P.PUBLIC and P.evaluate("GET", "/referral/validate", None).allowed
        assert P.classify("GET", "/referral/org-1") == P.AUTHENTICATED
        assert P.classify("GET", "/gen-dashboard/referrals") == P.OPERATOR
        assert P.classify("POST", "/gen-dashboard/referrals/rewards/3/void") == P.OPERATOR
        admin, manager, staff = (P.Identity("u", "o", r) for r in (1, 2, 3))
        assert P.evaluate("GET", "/referral/o", admin, has_permission=lambda n: True).allowed
        assert not P.evaluate("GET", "/referral/o", manager, has_permission=lambda n: True).allowed
        assert not P.evaluate("GET", "/referral/o", staff, has_permission=lambda n: True).allowed
        for identity in (admin, manager, staff):                       # operator routes are never satisfied by an app token
            assert not P.evaluate("POST", "/gen-dashboard/referrals/rewards/3/void", identity, has_permission=lambda n: True).allowed


# ═════════════════════════════════════════════════════════════════════════ operator
def seed_operator_data(env):
    db = env.db
    seed_referral(db, REF_ORG, ORG)                  # referral 1: Zed -> Acme (will be paid)
    seed_referral(db, REF_ORG, THIRD_ORG)            # referral 2: Zed -> Third (signed up)
    db.orgs["org-4"] = {"org_id": "org-4", "org_name": "Fourth Inc", "parent_id": "user-4"}
    seed_referral(db, THIRD_ORG, "org-4")            # referral 3: Third -> Fourth
    env.client.post("/orders/create", json=create_body(apply_referral=True))
    verify(env)                                      # ORG paid with its reward: referral 1 qualified, Zed's reward 2 AVAILABLE


class TestOperator:
    def op(self, env, lookup=None, enforce=True):
        if enforce:
            import os
            os.environ["OPERATOR_ENFORCE"] = "1"
        operator_auth.set_lookup(lookup or (lambda phone: {"1": 1, "2": 2}.get(phone)))

    def test_summary_top_referrers_and_rows(self, env):
        seed_operator_data(env)
        body = env.client.get("/gen-dashboard/referrals").json()
        assert body["summary"] == {"total_referrals": 3, "converted": 1, "rewards_issued": 6, "rewards_available": 3,
                                   "rewards_pending": 2, "rewards_used": 1, "discount_given": 720.0}
        assert body["top_referrers"][0] == {"org_id": REF_ORG, "org_name": "Zed Corp", "code": env.db.codes[REF_ORG], "referrals": 2, "converted": 1}
        assert [r["referral_id"] for r in body["referrals"]] == [3, 2, 1]                  # newest first
        first = body["referrals"][2]
        assert (first["referrer_org_name"], first["referred_org_name"], first["status"], first["code"]) == ("Zed Corp", "Acme", "paid", env.db.codes[REF_ORG])
        assert first["signed_up_at"].endswith("Z") and first["qualified_at"].endswith("Z")
        assert first["referrer_reward"]["status"] == "available" and first["referrer_reward"]["invoice_number"] is None
        assert first["referred_reward"]["status"] == "used" and first["referred_reward"]["plan_name"] == "Prime"
        assert first["referred_reward"]["discount_amount"] == 720.0 and first["referred_reward"]["invoice_number"] == env.db.invoices[0]["invoice_number"]
        assert set(first["referred_reward"]) == {"reward_id", "status", "used_at", "plan_name", "discount_amount", "invoice_number"}
        assert set(first) == {"referral_id", "code", "referrer_org_id", "referrer_org_name", "referred_org_id", "referred_org_name",
                              "signed_up_at", "qualified_at", "status", "referrer_reward", "referred_reward"}

    def test_status_filter_and_search(self, env):
        seed_operator_data(env)
        get = lambda **q: [r["referral_id"] for r in env.client.get("/gen-dashboard/referrals", params=q).json()["referrals"]]
        assert get(status="all") == [3, 2, 1] and get(status="paid") == [1] and get(status="signed_up") == [3, 2]
        assert get(q="acme") == [1] and get(q="zed") == [2, 1] and get(q="Fourth") == [3]
        assert get(q=env.db.codes[REF_ORG]) == [2, 1]                                      # the code on the referral
        assert get(q=env.db.codes[THIRD_ORG]) == [3, 2]                                    # the code on referral 3 AND Third's own code (referred in 2)
        assert get(q="zed", status="paid") == [1] and get(q="nothing-like-this") == []
        assert get(q="50%") == []                                                           # wildcards are literal
        assert env.client.get("/gen-dashboard/referrals", params={"status": "weird"}).status_code == 400

    def test_empty_database(self, env):
        body = env.client.get("/gen-dashboard/referrals").json()
        assert body["summary"] == {"total_referrals": 0, "converted": 0, "rewards_issued": 0, "rewards_available": 0,
                                   "rewards_pending": 0, "rewards_used": 0, "discount_given": 0.0}
        assert body["top_referrers"] == [] and body["referrals"] == []

    def test_void_available_and_pending(self, env):
        seed_operator_data(env)
        self.op(env)
        r = env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "1"})        # pending reward of Fourth's referrer
        assert r.status_code == 200 and r.json() == {"reward_id": 4, "status": "void"}
        pending = [x for x in env.db.rewards if x["reward_id"] == 4][0]
        assert pending["status"] == "VOID"
        assert env.client.post("/gen-dashboard/referrals/rewards/3/void", headers={"x-admin-id": "1"}).json()["status"] == "void"

    def test_void_a_used_or_void_reward_is_a_409_and_unknown_a_404(self, env):
        seed_operator_data(env)
        self.op(env)
        used = [x for x in env.db.rewards if x["status"] == "USED"][0]
        r = env.client.post(f"/gen-dashboard/referrals/rewards/{used['reward_id']}/void", headers={"x-admin-id": "1"})
        assert r.status_code == 409 and used["status"] == "USED"
        env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "1"})
        assert env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "1"}).status_code == 409
        assert env.client.post("/gen-dashboard/referrals/rewards/999/void", headers={"x-admin-id": "1"}).status_code == 404

    def test_void_is_super_admin_only_and_the_list_needs_an_operator(self, env):
        seed_operator_data(env)
        self.op(env)
        assert env.client.post("/gen-dashboard/referrals/rewards/4/void").status_code == 403                 # no credential
        assert env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "2"}).status_code == 403   # role 2
        assert env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "77"}).status_code == 403  # not an operator
        assert env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"authorization": "Bearer abc"}).status_code == 401   # an app token is not enough (not an operator session)
        assert [x for x in env.db.rewards if x["reward_id"] == 4][0]["status"] == "PENDING"
        assert env.client.get("/gen-dashboard/referrals").status_code == 403
        assert env.client.get("/gen-dashboard/referrals", headers={"x-admin-id": "2"}).status_code == 200      # any operator may read

    def test_the_void_update_is_guarded_by_status(self, env):
        seed_operator_data(env)
        self.op(env)
        env.client.post("/gen-dashboard/referrals/rewards/4/void", headers={"x-admin-id": "1"})
        sql = [x[0] for x in env.db.log if x[0].startswith("UPDATE referral_reward SET status = 'VOID'")][0]
        assert "status IN ('AVAILABLE', 'PENDING')" in sql


# ═════════════════════════════════════════════════════════════════════════ subscriptions list
class ListCursor:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append(norm(sql))

    def fetchall(self):
        return [dict(r) for r in self.rows]

    def close(self):
        pass


class TestSubscriptionsList:
    BASE = {"org_id": "o1", "org_name": "Acme", "admin_mobile": "9", "admin_email": "a@x", "plan_name": "Prime", "staff_limit": 15,
            "start_date": date(2026, 11, 2), "end_date": date(2027, 11, 2), "status": "ACTIVE", "invoice_number": "INV-2026-0001",
            "total_amount": D("7646.00"), "base_amount": D("6480.00"), "cgst": D("583.00"), "sgst": D("583.00"), "razorpay_payment_id": "pay_1",
            "payment_date": datetime(2026, 11, 2, 9), "valid_till": date(2027, 11, 2), "coupon_code": None, "invoice_user_id": "u1"}

    def run(self, monkeypatch, rows):
        import asyncio
        cur = ListCursor(rows)

        class Conn:
            def cursor(self, cursor_factory=None):
                return cur
        monkeypatch.setattr(dash_fn, "get_db_connection", lambda: Conn())
        monkeypatch.setattr(dash_fn, "return_connection", lambda c: None)
        return cur, asyncio.run(dash_fn.get_org_subscriptions_list())["data"]

    def test_new_keys_and_old_keys(self, monkeypatch):
        applied = {**self.BASE, "referral_applied": True, "referral_discount": D("720.00"), "referral_role": "referred", "referral_with_org_name": "Zed Corp"}
        plain = {**self.BASE, "org_id": "o2", "referral_applied": False, "referral_discount": None, "referral_role": None, "referral_with_org_name": None}
        _, data = self.run(monkeypatch, [applied, plain])
        a, b = data
        assert (a["referral_applied"], a["referral_discount"], a["referral_role"], a["referral_with_org_name"]) == (True, 720.0, "referred", "Zed Corp")
        assert (b["referral_applied"], b["referral_discount"], b["referral_role"], b["referral_with_org_name"]) == (False, None, None, None)
        for key in ("org_id", "org_name", "admin_mobile", "admin_email", "plan_name", "staff_limit", "start_date", "end_date", "status",
                    "invoice_number", "total_amount", "base_amount", "cgst", "sgst", "razorpay_payment_id", "payment_date", "valid_till",
                    "coupon_code", "invoice_user_id"):
            assert key in a                                                                  # the existing keys are unchanged
        assert a["total_amount"] == 7646.0 and a["start_date"] == "2026-11-02" and a["payment_date"].startswith("2026-11-02")

    def test_it_is_one_query_with_the_referral_joins_on_the_latest_invoice(self, monkeypatch):
        cur, _ = self.run(monkeypatch, [])
        assert len(cur.sql) == 1
        sql = cur.sql[0]
        assert "LEFT JOIN LATERAL" in sql and "referral_reward_id FROM gen_invoices gi2" in sql and "LIMIT 1 ) gi ON true" in sql
        assert "LEFT JOIN referral_reward rrw ON rrw.reward_id = gi.referral_reward_id" in sql
        assert "LEFT JOIN referral rf ON rf.referral_id = rrw.referral_id" in sql
        for alias in ("referral_applied", "referral_discount", "referral_role", "referral_with_org_name"):
            assert f"AS {alias}" in sql
        assert "WHERE ps.status = 'ACTIVE' ORDER BY ps.end_date ASC" in sql
