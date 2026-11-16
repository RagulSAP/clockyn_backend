"""Deleting an account (console: account deletion requests and Users) also removes the Refer & Earn data of its organisations.
core_functions/referral_functions.py::delete_org_referral_data, called from company_functions.delete_user_account (hard delete) and
data_lifecycle.anonymise_account (ACCOUNT_DELETE_ANONYMISE). DB-free: a small stateful fake plays the four statements.
Run: python -m pytest tests/test_referral_account_delete.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import data_lifecycle as dl  # noqa: E402
from core_functions import referral_functions as ref  # noqa: E402
from test_data_lifecycle import Events, _company, account_routes, make, s3  # noqa: E402,F401  (fixtures and helpers of the lifecycle tests)


class World:
    """referral / referral_reward / org_referral_code in memory, with just the statements delete_org_referral_data issues."""

    def __init__(self):
        self.codes = {"X": "XXXXXX", "Y": "YYYYYY", "Z": "ZZZZZZ"}
        self.referrals = []     # {referral_id, referrer_org_id, referred_org_id, partner_id, qualified_at, commission_amount}
        self.rewards = []       # {reward_id, referral_id, org_id, role, status}
        self.log, self.rowcount, self.fail_on = [], 0, None

    def referral(self, rid, referrer, referred, paid=False, partner=None, commission=None):
        self.referrals.append({"referral_id": rid, "referrer_org_id": referrer, "referred_org_id": referred, "partner_id": partner,
                               "qualified_at": "t" if paid else None, "commission_amount": commission})

    def reward(self, rid, referral_id, org, role, status):
        self.rewards.append({"reward_id": rid, "referral_id": referral_id, "org_id": org, "role": role, "status": status})

    def by_id(self, rid):
        return next((r for r in self.rewards if r["reward_id"] == rid), None)

    # the cursor
    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.log.append((s, params))
        if self.fail_on and self.fail_on in s:
            raise RuntimeError("boom")
        self.rowcount = 0
        if s.startswith("UPDATE referral_reward SET status = 'VOID'"):
            ids = params[0]
            waiting = {r["referral_id"] for r in self.referrals if r["referred_org_id"] in ids and r["qualified_at"] is None}
            for w in self.rewards:
                if w["role"] == "referrer" and w["status"] == "PENDING" and w["referral_id"] in waiting:
                    w["status"] = "VOID"
                    self.rowcount += 1
        elif s.startswith("DELETE FROM referral_reward WHERE org_id = ANY"):
            keep = [w for w in self.rewards if w["org_id"] not in params[0]]
            self.rowcount, self.rewards = len(self.rewards) - len(keep), keep
        elif s.startswith("DELETE FROM org_referral_code WHERE org_id = ANY"):
            for org in [o for o in self.codes if o in params[0]]:
                del self.codes[org]
                self.rowcount += 1
        elif s.startswith("DELETE FROM referral r WHERE"):
            ids = params[0]
            for r in list(self.referrals):
                if r["partner_id"] is None and (r["referrer_org_id"] in ids or r["referred_org_id"] in ids) \
                        and not any(w["referral_id"] == r["referral_id"] for w in self.rewards):
                    self.referrals.remove(r)
                    self.rowcount += 1


@pytest.fixture
def w():
    return World()


def run(world, orgs):
    return ref.delete_org_referral_data(world, orgs)


def test_the_organisations_own_code_and_rewards_are_deleted(w):
    w.referral(1, "X", "Y")
    w.reward(1, 1, "X", "referrer", "PENDING")
    w.reward(2, 1, "Y", "referred", "AVAILABLE")
    out = run(w, ["X"])
    assert "X" not in w.codes and w.codes["Y"] == "YYYYYY"                        # its code is gone, the others stay
    assert w.by_id(1) is None                                                       # X's own reward is gone
    assert out["codes"] == 1 and out["rewards"] == 1


def test_a_friend_the_deleted_organisation_referred_keeps_their_own_discount(w):
    w.referral(1, "X", "Y")                                                         # X referred Y, Y is still a customer
    w.reward(1, 1, "X", "referrer", "PENDING")
    w.reward(2, 1, "Y", "referred", "AVAILABLE")
    run(w, ["X"])
    assert w.by_id(2)["status"] == "AVAILABLE"                                      # Y's reward is untouched
    assert [r["referral_id"] for r in w.referrals] == [1]                           # the row stays: Y's reward needs it (foreign key)


def test_a_reward_waiting_for_a_deleted_organisation_that_never_paid_is_voided(w):
    w.referral(1, "Y", "X", paid=False)                                             # Y referred X, X deleted before its first payment
    w.reward(1, 1, "X", "referred", "AVAILABLE")
    w.reward(2, 1, "Y", "referrer", "PENDING")
    out = run(w, ["X"])
    assert w.by_id(1) is None and w.by_id(2)["status"] == "VOID" and out["voided"] == 1
    assert [r["referral_id"] for r in w.referrals] == [1]                           # kept: Y's (void) reward still hangs on it


def test_a_reward_the_friend_already_earned_is_not_taken_away(w):
    w.referral(1, "Y", "X", paid=True)                                              # X paid, so Y's reward unlocked
    w.reward(1, 1, "X", "referred", "USED")
    w.reward(2, 1, "Y", "referrer", "AVAILABLE")
    run(w, ["X"])
    assert w.by_id(2)["status"] == "AVAILABLE"                                      # Y keeps what it earned
    assert w.by_id(1) is None


def test_when_both_sides_are_deleted_the_referral_row_goes_too(w):
    w.referral(1, "X", "Y")
    w.reward(1, 1, "X", "referrer", "PENDING")
    w.reward(2, 1, "Y", "referred", "AVAILABLE")
    out = run(w, ["X", "Y"])
    assert w.referrals == [] and w.rewards == [] and out["referrals"] == 1


def test_a_promoter_referral_row_is_kept_because_it_carries_the_commission(w):
    w.referral(5, None, "X", paid=True, partner=3, commission=612)
    w.reward(9, 5, "X", "referred", "USED")
    run(w, ["X"])
    assert w.by_id(9) is None                                                       # the customer's reward goes
    assert len(w.referrals) == 1 and w.referrals[0]["commission_amount"] == 612     # the promoter's earned commission stays


def test_other_organisations_are_untouched(w):
    w.referral(1, "Y", "Z")
    w.reward(1, 1, "Z", "referred", "AVAILABLE")
    w.reward(2, 1, "Y", "referrer", "PENDING")
    run(w, ["X"])
    assert w.codes.keys() == {"Y", "Z"} and len(w.rewards) == 2 and w.by_id(2)["status"] == "PENDING"


def test_no_organisation_means_no_statement(w):
    assert run(w, []) == {"voided": 0, "rewards": 0, "codes": 0, "referrals": 0} and w.log == []
    assert run(w, [None, ""]) == {"voided": 0, "rewards": 0, "codes": 0, "referrals": 0} and w.log == []


def test_a_database_error_never_fails_the_deletion(w):
    w.reward(1, 1, "X", "referrer", "PENDING")
    w.fail_on = "DELETE FROM org_referral_code"
    out = run(w, ["X"])
    assert out == {"voided": 0, "rewards": 0, "codes": 0, "referrals": 0}
    statements = [s for s, _ in w.log]
    assert statements[0] == "SAVEPOINT referral_cleanup_sp" and statements[-1] == "ROLLBACK TO SAVEPOINT referral_cleanup_sp"


def test_everything_runs_in_one_savepoint_and_the_ids_are_passed_as_a_list(w):
    run(w, ["X", "X", "Y"])                                                         # duplicates are collapsed
    statements = [s for s, _ in w.log]
    assert statements[0] == "SAVEPOINT referral_cleanup_sp" and statements[-1] == "RELEASE SAVEPOINT referral_cleanup_sp"
    assert [p for s, p in w.log if s.startswith("DELETE FROM org_referral_code")] == [(["X", "Y"],)]


# ─── both deletion paths call it, inside their own transaction ────────────────────────────────────────────────────────
def test_the_anonymise_path_deletes_the_referral_data_of_every_organisation_before_blanking_them(s3):
    events = Events()
    cur, _ = make(events, account_routes())
    dl.anonymise_account(cur, "owner")
    deletes = cur.find("DELETE FROM org_referral_code")
    assert [p for _s, p in deletes] == [(["orgA", "orgB"],)]
    sqls = [s for s, _p in cur.sql]
    assert sqls.index("SAVEPOINT referral_cleanup_sp") < next(i for i, s in enumerate(sqls) if s.startswith("UPDATE org_info SET org_name"))
    assert cur.find("DELETE FROM referral_reward WHERE org_id = ANY(%s)")
    assert not cur.find("DELETE FROM gen_")                                       # billing is still never touched


def test_the_hard_delete_path_deletes_the_referral_data_before_the_organisation_row(monkeypatch, s3):
    monkeypatch.delenv("ACCOUNT_DELETE_ANONYMISE", raising=False)
    events = Events()
    cur, conn = make(events, [("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}]),
                              ("SELECT user_id FROM users WHERE parent_id", [{"user_id": "s1"}])])
    cf = _company(monkeypatch, cur, conn)
    asyncio.run(cf.delete_user_account("owner"))
    sqls = [s for s, _p in cur.sql]
    first_referral = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM org_referral_code"))
    org_delete = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM org_info WHERE org_id"))
    assert first_referral < org_delete
    assert [p for _s, p in cur.find("DELETE FROM org_referral_code")] == [(["orgA"],)]
    assert [e[0] for e in events if e[0] in ("commit", "rollback")] == ["commit"]        # still one commit


def test_a_failing_referral_cleanup_does_not_stop_the_hard_delete(monkeypatch, s3):
    monkeypatch.delenv("ACCOUNT_DELETE_ANONYMISE", raising=False)
    events = Events()
    cur, conn = make(events, [("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}])], fail_on="DELETE FROM org_referral_code")
    cf = _company(monkeypatch, cur, conn)
    res = asyncio.run(cf.delete_user_account("owner"))
    assert res["success"] is True and cur.find("DELETE FROM org_info WHERE org_id")           # the deletion went on
