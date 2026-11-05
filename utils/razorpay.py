import razorpay
import hmac
import hashlib
import time
from decimal import Decimal, ROUND_HALF_UP
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor

def _paise(amount) -> int:
    """Rupees (int / float / Decimal / str) to integer paise, rounded half up (int(19.99 * 100) would give 1998)."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str):
        self.key_id = key_id
        self.key_secret = key_secret
        self.client = razorpay.Client(auth=(key_id, key_secret))
        
    def create_order(
        self,
        amount: float,
        currency: str = "INR",
        receipt: str | None = None,
        notes: dict | None = None,
    ):
        order_data = {
            'amount': _paise(amount),
            'currency': currency,
            'payment_capture': 1
        }
        if receipt:
            order_data['receipt'] = receipt
        if notes:
            order_data['notes'] = notes
        return self.client.order.create(order_data)
    
    def verify_payment_signature(self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str):
        generated_signature = hmac.new(
            self.key_secret.encode(),
            f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()
        # bytes comparison: compare_digest raises TypeError on a non-ASCII str (the caller would answer 500)
        return hmac.compare_digest(generated_signature.encode(), str(razorpay_signature or "").encode())
    
def verify_webhook_signature(raw_body: bytes, signature, secret: str) -> bool:
    """HMAC-SHA256 hex of the EXACT raw request bytes with the webhook secret, compared in constant time.

    False for a missing signature or an empty secret (fail closed: an empty key would make the HMAC forgeable).
    """
    if not secret or not signature or not isinstance(raw_body, (bytes, bytearray)):
        return False
    expected = hmac.new(secret.encode(), bytes(raw_body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), str(signature).strip().encode())


_razorpay_cache: dict = {"client": None, "key_id": None, "expires_at": 0.0}

def get_razorpay_client():
    now = time.monotonic()
    if _razorpay_cache["client"] and now < _razorpay_cache["expires_at"]:
        return _razorpay_cache["client"], _razorpay_cache["key_id"]

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT key_id, key_secret
            FROM gen_razorpay_key
            WHERE status = 1
            LIMIT 1
        """)
        row = cursor.fetchone()
    finally:
        cursor.close()
        return_connection(conn)

    if not row:
        raise Exception("Razorpay key not found in database")

    client = RazorpayClient(row["key_id"], row["key_secret"])
    _razorpay_cache["client"] = client
    _razorpay_cache["key_id"] = row["key_id"]
    _razorpay_cache["expires_at"] = now + 300  # 5-minute TTL
    return client, row["key_id"]
