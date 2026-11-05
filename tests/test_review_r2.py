"""Regression tests found by the independent review R2 (DB-free).

Run: python -m pytest tests/test_review_r2.py -q
"""
import asyncio
import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ------------------------------------------------------------------ IGST invoices must keep their tax line
class _Cur:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sqls = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, cursor_factory=None):
        return self.cur


INVOICE_ROW = {
    "customer_name": "C", "org_name": "O", "org_address": "A", "state": "Karnataka", "district": "D", "pincode": "1",
    "org_id": "o1", "invoice_number": "INV-2026-0001", "plan_name": "Core", "staff_count": 10,
    "base_amount": 7200, "cgst": 0, "sgst": 0, "igst": 1296, "total_amount": 8496,
    "payment_id": "pay_1", "order_id": "order_1",
    "payment_date": datetime(2026, 1, 5), "valid_till": datetime(2027, 1, 5),
}


def test_invoice_download_prints_igst_for_an_inter_state_invoice(monkeypatch):
    from utils import email as em
    cur = _Cur([dict(INVOICE_ROW), None])
    seen = []
    monkeypatch.setattr(em, "get_db_connection", lambda: _Conn(cur))
    monkeypatch.setattr(em, "return_connection", lambda c: None)
    monkeypatch.setattr(em, "_authorize_invoice_download", lambda request: "open")
    monkeypatch.setattr(em, "_generate_invoice_pdf", lambda data: seen.append(data) or b"%PDF")
    asyncio.run(em.download_invoice(None, user_id="u1", org_id="o1", invoice_number="INV-2026-0001"))
    assert "gi.igst" in cur.sqls[0]
    data = seen[0]
    assert data["igst"] == 1296.0 and data["cgst"] == 0 and data["sgst"] == 0
    assert data["igst_pct"] == 18
    # the printed lines add up to the total (base + igst)
    assert data["base_amount"] + data["igst"] + data["cgst"] + data["sgst"] == data["total_amount"]


def test_bulk_download_prints_igst_for_an_inter_state_invoice(monkeypatch):
    from utils import email as em
    row = dict(INVOICE_ROW, customer_email="c@x.test", customer_mobile="9", org_gst="", coupon_code=None)
    cur = _Cur([row])
    seen = []
    monkeypatch.setattr(em, "get_db_connection", lambda: _Conn(cur))
    monkeypatch.setattr(em, "return_connection", lambda c: None)
    monkeypatch.setattr(em, "_generate_invoice_pdf", lambda data: seen.append(data) or b"%PDF")
    resp = asyncio.run(em.bulk_download_invoices("2026-01-01", "2026-01-31"))
    assert "gi.igst" in cur.sqls[0]
    assert seen[0]["igst"] == 1296.0 and seen[0]["igst_pct"] == 18
    assert zipfile.ZipFile(io.BytesIO(resp.body)).namelist()


# ------------------------------------------------------------------ signature check must not raise on odd input
def test_payment_signature_check_returns_false_for_non_ascii_or_missing_signature():
    import hashlib
    import hmac
    from utils.razorpay import RazorpayClient
    client = RazorpayClient.__new__(RazorpayClient)
    client.key_secret = "secret"
    good = hmac.new(b"secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    assert client.verify_payment_signature("order_1", "pay_1", good) is True
    assert client.verify_payment_signature("order_1", "pay_1", "sigé") is False     # was a TypeError -> HTTP 500
    assert client.verify_payment_signature("order_1", "pay_1", None) is False
    assert client.verify_payment_signature("order_1", "pay_1", "") is False
