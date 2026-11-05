"""DB-free tests for the face reliability changes (Tier A), now on the face service facade (AWS Rekognition).

The face service (ml/face_service.py) is replaced by fakes and the DB layer is monkeypatched, so no AWS call, S3 or
PostgreSQL is needed; the behavioural assertions (response codes, reasons, force_accept, geofence, metrics line) are the
ones of the former insightface version. Run: python -m pytest tests/test_face_tier_a.py -q
"""
import asyncio
import importlib
import io
import json
import logging
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- stubs
def _install_stubs():
    # No ML library is needed any more (matching is AWS Rekognition; cv2 / onnxruntime are imported lazily and only for
    # the optional anti-spoof model). Only the environment helpers below are stubbed when they cannot be imported here.
    try:  # Windows without tzdata cannot build ZoneInfo objects: stub the helpers then
        import utils.timezone  # noqa: F401
    except Exception:
        tz = types.ModuleType("utils.timezone")
        ist = timezone(timedelta(hours=5, minutes=30))
        tz.IST, tz.UTC = ist, timezone.utc
        tz.get_utc_now = lambda: datetime.now(timezone.utc)
        tz.convert_utc_to_ist = lambda dt: dt.astimezone(ist)
        tz.format_ist_time = lambda dt, fmt="%I:%M %p": dt.astimezone(ist).strftime(fmt)
        sys.modules["utils.timezone"] = tz
        pkg = sys.modules.get("utils")
        if pkg is not None:
            pkg.timezone = tz
    try:
        import core_functions.auth_function  # noqa: F401
    except Exception:
        auth = types.ModuleType("core_functions.auth_function")

        async def _send(**kw):
            return None

        auth.send_punch_in_out_notification = _send
        sys.modules["core_functions.auth_function"] = auth


_install_stubs()

import ml.face as mlface  # noqa: E402
import ml.face_quality as fq  # noqa: E402
import ml.face_service as fs  # noqa: E402
import core_functions.face_metrics as fm  # noqa: E402
import core_functions.face_attendance_functions as faf  # noqa: E402


# --------------------------------------------------------------------------- helpers
def _png(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def _noise(w, h, lo=0, hi=255, seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(lo, hi, (h, w, 3), dtype=np.uint8)


class FakeUpload:
    def __init__(self, data=b"x"):
        self.data = data

    async def read(self):
        return self.data


class FakeCursor:
    def __init__(self, st):
        self.st = st
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.st["sqls"].append(self.sql)

    def fetchone(self):
        s, st = self.sql, self.st
        if "FROM users u" in s:
            return st.get("user")
        if "SELECT id, punch_in FROM attendance" in s:  # open-session lookup (also carries punch_in for the 18 h rule)
            return st.get("open")
        if "COUNT(*)" in s:
            return {"cnt": st.get("cnt", 0)}
        if "RETURNING id" in s:
            return {"id": 42}
        if "SELECT id, punch_in, attendance_date" in s:
            return st.get("record")
        if "SUM(work_hours)" in s:
            return {"prev_hours": 0}
        return None

    def close(self):
        pass


class FakeConn:
    def __init__(self, st):
        self.st = st

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.st)

    def commit(self):
        self.st["commits"] += 1

    def rollback(self):
        self.st["rollbacks"] += 1


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FACE_ATTEMPT_LOG", "0")  # never touch a DB unless a test opts in
    yield


GOOD_QUALITY = {"w": 640, "h": 480, "brightness": 120.0, "blur": 90.0}


def prepared_ok(quality=None):
    """What `prepare_face` returns for a usable photo (the tests that do not care about decoding patch it in)."""
    return {"success": True, "jpeg": b"jpeg", "image": None, "quality": dict(quality or GOOD_QUALITY)}


class FakeFaceBackend:
    """Scripted face service backend: nothing leaves the process."""
    name = "fake"

    def __init__(self, registered=True, verify=None, register=None):
        self.registered = registered
        self._verify = verify
        self._register = register
        self.verify_calls = []
        self.register_calls = []

    def is_registered(self, user_id):
        return self.registered

    def verify(self, user_id, photo, spoof_check=None):
        self.verify_calls.append((user_id, photo))
        return self._verify or fs.VerifyResult(True, 81.0, True, "ok")

    def register(self, user_id, photos):
        self.register_calls.append((user_id, list(photos)))
        return self._register or fs.RegisterResult(True, "ok", None, "", None, ["f1", "f2", "f3", "f4"])

    def remove(self, user_id):
        return fs.RemoveResult()

    def warmup(self):
        return True


@pytest.fixture
def db(monkeypatch):
    st = {
        "sqls": [], "commits": 0, "rollbacks": 0, "cnt": 0, "open": None, "record": None,
        "user": {"is_field_staff": True, "branch_id": "b1", "role_id": 1, "org_id": "o1", "parent_id": "o1",
                 "branch_lng": None, "branch_lat": None, "punch_radius": 100},
    }
    monkeypatch.setattr(faf, "get_db_connection", lambda: FakeConn(st))
    monkeypatch.setattr(faf, "return_connection", lambda c: None)
    monkeypatch.setattr(faf, "save_punch_image", lambda *a, **k: None)
    fs.set_backend(FakeFaceBackend(registered=True))      # the user has a registered face
    monkeypatch.setattr(faf, "prepare_face", lambda b, for_enroll=False: prepared_ok())
    monkeypatch.setattr(faf, "verify_face", lambda prepared, u: {"match": True, "score": 0.81})
    import core_functions.holiday_functions as hol
    monkeypatch.setattr(hol, "check_holiday_block", lambda o, u: None)
    return st


def punch(action="punch_in", force=False, lat=None, lng=None, data=b"img"):
    return asyncio.run(faf.punch_with_face("u1", "o1", FakeUpload(data), action, lat, lng, force))


# --------------------------------------------------------------------------- ml.face knobs
def test_exif_transpose_applied():
    img = Image.fromarray(_noise(40, 20))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 270 -> displayed 20 wide x 40 tall
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    out = mlface.load_image(buf.getvalue())
    assert out.shape[:2] == (40, 20)


def test_width_env(monkeypatch):
    try:
        assert mlface.FACE_MAX_WIDTH == 640
        assert mlface.load_image(_png(_noise(800, 400))).shape[1] == 640
        monkeypatch.setenv("FACE_MAX_WIDTH", "300")
        importlib.reload(mlface)
        assert mlface.FACE_MAX_WIDTH == 300
        assert mlface.load_image(_png(_noise(600, 400))).shape[1] == 300
        monkeypatch.setenv("FACE_MAX_WIDTH", "abc")
        importlib.reload(mlface)
        assert mlface.FACE_MAX_WIDTH == 640
    finally:
        monkeypatch.undo()
        importlib.reload(mlface)


# --------------------------------------------------------------------------- reason classifier
def test_reason_classifier():
    dark = fq.measure_quality(np.zeros((300, 300, 3), dtype=np.uint8))
    assert fq.classify_failure_reason(dark) == "too_dark"
    blurry = fq.measure_quality(np.full((300, 300, 3), 140, dtype=np.uint8))
    assert blurry["blur"] == 0 and fq.classify_failure_reason(blurry) == "too_blurry"
    small = fq.measure_quality(_noise(100, 100, 60, 200))
    assert fq.classify_failure_reason(small) == "too_small"
    good = fq.measure_quality(_noise(300, 300, 60, 200))
    assert fq.classify_failure_reason(good) == "unknown"
    assert fq.classify_failure_reason({}) == "unknown"


def test_prepare_face_hard_precheck_only_refuses_degenerate_photos(monkeypatch):
    monkeypatch.setenv("FACE_BACKEND", "rekognition")        # the pre-check is on for Rekognition (no AWS call is made here)
    monkeypatch.delenv("FACE_PRECHECK", raising=False)
    black = mlface.prepare_face(_png(np.zeros((300, 300, 3), dtype=np.uint8)))
    assert (black["success"], black["code"], black["reason"], black["message"]) == (False, "no_face", "too_dark", "No face detected")
    flat = mlface.prepare_face(_png(np.full((300, 300, 3), 140, dtype=np.uint8)))
    assert (flat["code"], flat["reason"]) == ("no_face", "too_blurry")
    tiny = mlface.prepare_face(_png(_noise(60, 60, 60, 200)))
    assert (tiny["code"], tiny["reason"]) == ("no_face", "too_small")
    garbage = mlface.prepare_face(b"garbage")
    assert (garbage["code"], garbage["reason"], garbage["message"]) == ("image_unreadable", "unknown", "Face not captured properly")
    # dim but not degenerate (below the diagnostic dark threshold of 45): NEVER refused locally, the service decides
    dim = mlface.prepare_face(_png(_noise(300, 300, 20, 60)))
    assert dim["success"] and "reason" not in dim and dim["quality"]["brightness"] < fq.DARK_LUMINANCE
    assert dim["jpeg"][:2] == b"\xff\xd8" and dim["image"].shape == (300, 300, 3)
    # switch off / on explicitly, and the mock backend skips it by default
    monkeypatch.setenv("FACE_PRECHECK", "0")
    assert mlface.prepare_face(_png(np.zeros((300, 300, 3), dtype=np.uint8)))["success"]
    monkeypatch.delenv("FACE_PRECHECK")
    monkeypatch.setenv("FACE_BACKEND", "mock")
    assert mlface.prepare_face(_png(np.zeros((300, 300, 3), dtype=np.uint8)))["success"]
    monkeypatch.setenv("FACE_PRECHECK", "1")
    assert mlface.prepare_face(_png(np.zeros((300, 300, 3), dtype=np.uint8)))["reason"] == "too_dark"


def test_reason_for_prefers_the_service_reason_then_the_pixels():
    dark = fq.measure_quality(np.zeros((300, 300, 3), dtype=np.uint8))
    assert mlface.reason_for(dark) == "too_dark"
    assert mlface.reason_for(dark, "multiple_faces") == "multiple_faces"
    assert mlface.reason_for({}) == "unknown"


# --------------------------------------------------------------------------- punch codes
def test_codes_success_and_business_failures(db, monkeypatch):
    r = punch()
    assert r["success"] and r["code"] == "ok" and r["attendance_id"] == 42 and db["commits"] == 1

    db["open"] = {"id": 1, "punch_in": datetime.now(timezone.utc) - timedelta(hours=1)}
    r = punch()
    assert (r["success"], r["code"], r["message"]) == (False, "already_punched_in", "Already punched in. Please punch out first.")
    db["open"] = None
    db["cnt"] = 2
    r = punch()
    assert r["code"] == "max_sessions" and r["message"] == "Maximum 2 punch-in sessions allowed per day."
    db["cnt"] = 0

    r = punch("punch_out")
    assert r["code"] == "no_active_punch" and r["message"] == "No active punch in found"
    r = punch("dance")
    assert r["code"] == "invalid_action" and r["message"] == "Invalid action"

    # geofence: branch ~ (12.0, 77.0); 100 m radius
    db["user"].update(is_field_staff=False, branch_lng=77.0, branch_lat=12.0)
    r = punch(lat=12.01, lng=77.0)
    assert r["code"] == "out_of_range" and "away from your branch" in r["message"]
    r = punch()
    assert r["code"] == "location_required" and r["message"] == "Location access is required to punch in/out."
    r = punch(lat=12.0, lng=77.0)
    assert r["success"] and r["code"] == "ok"
    # force_accept behaviour unchanged: skips matching, flagged, geofence still applies
    r = punch(force=True, lat=12.0, lng=77.0)
    assert r["success"] and r["face_mismatch"] is True
    assert punch(force=True, lat=12.01, lng=77.0)["code"] == "out_of_range"


def test_codes_holiday_not_registered_face_failures(db, monkeypatch):
    import core_functions.holiday_functions as hol
    monkeypatch.setattr(hol, "check_holiday_block", lambda o, u: "Diwali")
    r = punch()
    assert r["code"] == "holiday_blocked" and r["is_holiday"] is True and "Diwali" in r["message"]
    assert punch("punch_out")["code"] != "holiday_blocked"
    monkeypatch.setattr(hol, "check_holiday_block", lambda o, u: None)

    fs.set_backend(FakeFaceBackend(registered=False))
    r = punch()
    assert r["code"] == "not_registered" and r["message"] == "Face not registered. Please register your face first."
    r = punch(force=True)                         # force_accept still needs a registered face (as before)
    assert r["code"] == "not_registered"

    fs.set_backend(FakeFaceBackend(registered=True))
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": 0.31, "code": "no_match"})
    r = punch()
    assert r["code"] == "no_match" and r["message"] == "Face not recognized" and "reason" not in r

    # the service refuses the photo: same code / reason vocabulary as a failed detection
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": None, "code": "no_face",
                                                         "reason": "multiple_faces", "message": "More than one face detected."})
    r = punch()
    assert (r["code"], r["reason"], r["message"]) == ("no_face", "multiple_faces", "More than one face detected.")
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": None, "code": "no_face", "message": "No face detected"})
    monkeypatch.setattr(faf, "prepare_face", lambda b, for_enroll=False: prepared_ok({"w": 100, "h": 100, "brightness": 120.0, "blur": 90.0}))
    r = punch()
    assert (r["code"], r["reason"]) == ("no_face", "too_small")           # derived from the local pixels
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": None, "code": "unavailable",
                                                         "message": "The face service is busy. Please try again in a moment."})
    r = punch()
    assert r["success"] is False and r["code"] == "server_error" and "try again" in r["message"]
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": None, "code": "server_error"})
    r = punch()
    assert (r["code"], r["message"]) == ("server_error", faf.GENERIC_ERROR_MESSAGE)
    monkeypatch.setattr(faf, "verify_face", lambda p, u: {"match": False, "score": None, "code": "spoof_detected",
                                                         "message": "Spoof detected. Please use a real face."})
    assert punch()["code"] == "spoof_detected"


def test_codes_for_unreadable_and_dark_photos_with_the_real_prepare_step(db, monkeypatch):
    monkeypatch.setenv("FACE_BACKEND", "rekognition")     # pre-check on; a pinned fake backend is used, no AWS call
    monkeypatch.delenv("FACE_PRECHECK", raising=False)
    import core_functions.face_recognition as fr       # the db fixture patched prepare_face: put the real one back
    monkeypatch.setattr(faf, "prepare_face", fr.prepare_face)
    r = punch(data=b"garbage")
    assert (r["code"], r["reason"], r["message"]) == ("image_unreadable", "unknown", "Face not captured properly")
    r = punch(data=_png(np.zeros((300, 300, 3), dtype=np.uint8)))
    assert (r["code"], r["reason"], r["message"]) == ("no_face", "too_dark", "No face detected")
    assert r["success"] is False


def test_server_error_is_generic_and_logged(db, monkeypatch, caplog):
    def boom(sql, params=None):
        raise RuntimeError("secret sql detail")
    monkeypatch.setattr(FakeCursor, "execute", lambda self, sql, params=None: boom(sql))
    r = punch()
    assert r["success"] is False and r["code"] == "server_error"
    assert "secret" not in json.dumps(r) and db["rollbacks"] == 1


# --------------------------------------------------------------------------- notification
def test_notification_failure_does_not_fail_punch(db, monkeypatch):
    db["user"]["role_id"] = 3
    calls = []

    async def failing_send(**kw):
        calls.append(kw)
        raise RuntimeError("fcm down")

    monkeypatch.setattr(faf, "send_punch_in_out_notification", failing_send)

    async def run():
        res = await faf.punch_with_face("u1", "o1", FakeUpload(b"i"), "punch_in", None, None, False)
        await asyncio.sleep(0.05)  # let the fire-and-forget notification run (and fail quietly)
        return res

    r = asyncio.run(run())
    assert r["success"] is True and r["code"] == "ok" and r["attendance_id"] == 42
    assert len(calls) == 1 and calls[0]["image_url"] is None and calls[0]["punch_type"] == "punch_in"


def test_notification_scheduling_error_does_not_fail_punch(db, monkeypatch):
    db["user"]["role_id"] = 2

    def broken(**kw):
        raise RuntimeError("cannot schedule")

    monkeypatch.setattr(faf, "_schedule_notification", broken)
    r = punch()
    assert r["success"] is True and r["code"] == "ok"


# --------------------------------------------------------------------------- offload
def test_inference_runs_off_event_loop_thread(db, monkeypatch):
    seen = {}

    def enc(b, for_enroll=False):
        seen["thread"] = threading.get_ident()
        return prepared_ok()

    monkeypatch.setattr(faf, "prepare_face", enc)
    r = punch()
    assert r["success"] and seen["thread"] != threading.get_ident()


def test_concurrency_is_bounded(monkeypatch):
    monkeypatch.setenv("FACE_MAX_CONCURRENCY", "2")
    monkeypatch.setattr(fm, "_executor", None)
    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def work(i):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return i

    async def run():
        return await asyncio.gather(*[fm.run_inference(work, i) for i in range(6)])

    assert sorted(asyncio.run(run())) == list(range(6))
    assert state["max"] == 2
    fm._executor.shutdown()
    monkeypatch.setattr(fm, "_executor", None)


# --------------------------------------------------------------------------- warm-up route
def test_warmup_route(monkeypatch):
    asked = []

    class Reg(FakeFaceBackend):
        def is_registered(self, user_id):
            asked.append(user_id)
            return user_id == "reg"

    fs.set_backend(Reg())
    r = asyncio.run(faf.warmup_user("reg"))
    assert r["success"] is True and r["registered"] is True and isinstance(r["ms"], int)
    r = asyncio.run(faf.warmup_user("nobody"))
    assert r["success"] is True and r["registered"] is False
    assert asked == ["reg", "nobody"]              # nothing is cached in the process any more

    class Broken(FakeFaceBackend):
        def is_registered(self, user_id):
            raise RuntimeError("db down")

    fs.set_backend(Broken())
    r = asyncio.run(faf.warmup_user("reg"))
    assert r["success"] is False and r["registered"] is False      # never raises (best effort), as before
    fs.set_backend(Reg())

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from router import face_attendance as rt
    except Exception as e:  # router pulls in unrelated modules; the function test above is the core check
        pytest.skip(f"router not importable here: {e}")
    app = FastAPI()
    app.include_router(rt.router)
    resp = TestClient(app).get("/face/warmup/reg")
    assert resp.status_code == 200 and resp.json()["registered"] is True


# --------------------------------------------------------------------------- register
def test_register_failure_body_shape(monkeypatch):
    monkeypatch.setenv("FACE_BACKEND", "rekognition")        # local pre-check on; the face service itself is a pinned fake
    monkeypatch.delenv("FACE_PRECHECK", raising=False)
    backend = FakeFaceBackend()
    fs.set_backend(backend)
    files = [FakeUpload(_png(np.zeros((300, 300, 3), dtype=np.uint8))) for _ in range(4)]
    r = asyncio.run(faf.register_face("u1", "o1", files))
    assert r.status_code == 400
    assert json.loads(r.body) == {"detail": "No face detected", "code": "no_face", "reason": "too_dark"}

    r = asyncio.run(faf.register_face("u1", "o1", [FakeUpload(b"garbage")] * 4))
    assert r.status_code == 400
    assert json.loads(r.body) == {"detail": "Face not captured properly", "code": "image_unreadable", "reason": "unknown"}

    r = asyncio.run(faf.register_face("u1", "o1", files[:2]))
    assert r.status_code == 400 and json.loads(r.body)["detail"] == "Please provide at least 4 face images"
    assert backend.register_calls == []                      # nothing reached the service (no paid call for hopeless photos)


def _good_files(n=4):
    return [FakeUpload(_png(_noise(320, 320, 60, 200, seed=i))) for i in range(n)]


def test_register_maps_service_failures_and_success(monkeypatch):
    import ml.s3 as s3mod
    saved = []
    monkeypatch.setattr(s3mod, "save_employee_profile", lambda uid, b: saved.append((uid, len(b))))

    def run(result):
        backend = FakeFaceBackend(register=result)
        fs.set_backend(backend)
        return asyncio.run(faf.register_face("u1", "o1", _good_files())), backend

    r, backend = run(None)                                    # success: 4 JPEGs go to the service, profile photo saved
    assert r == {"success": True, "message": "Face registered successfully", "code": "ok"}
    assert [len(p) > 0 for p in backend.register_calls[0][1]] == [True] * 4 and backend.register_calls[0][0] == "u1"
    assert all(p[:2] == b"\xff\xd8" for p in backend.register_calls[0][1]) and saved and saved[0][0] == "u1"

    # the service refuses photo 2: 400 with the old body shape; the reason comes from the local pixels when it gave none
    r, _ = run(fs.RegisterResult(False, "no_face", None, "No face detected", 2))
    assert r.status_code == 400 and json.loads(r.body) == {"detail": "No face detected", "code": "no_face", "reason": "unknown"}
    r, _ = run(fs.RegisterResult(False, "no_face", "multiple_faces", "More than one face detected.", 0))
    assert json.loads(r.body) == {"detail": "More than one face detected.", "code": "no_face", "reason": "multiple_faces"}
    r, _ = run(fs.RegisterResult(False, "no_face", "too_dark", "No face detected", 1))
    assert json.loads(r.body)["reason"] == "too_dark"
    r, _ = run(fs.RegisterResult(False, "image_unreadable", "unknown", "Face not captured properly", 1))
    assert json.loads(r.body) == {"detail": "Face not captured properly", "code": "image_unreadable", "reason": "unknown"}

    # throttled / unreachable service: 503 "try again" (code server_error, which the app already knows)
    r, _ = run(fs.RegisterResult(False, "unavailable", None, fs.BUSY_MESSAGE))
    assert r.status_code == 503 and json.loads(r.body) == {"detail": fs.BUSY_MESSAGE, "code": "server_error"}
    # configuration / unexpected failure: generic 500, nothing leaks
    r, _ = run(fs.RegisterResult(False, "server_error", None, "secret iam detail"))
    body = json.loads(r.body)
    assert r.status_code == 500 and body["code"] == "server_error" and "secret" not in json.dumps(body)

    class Boom(FakeFaceBackend):
        def register(self, user_id, photos):
            raise RuntimeError("s3 exploded")

    fs.set_backend(Boom())
    r = asyncio.run(faf.register_face("u1", "o1", _good_files()))
    assert r.status_code == 500 and "exploded" not in json.dumps(json.loads(r.body))


def test_profile_photo_failure_does_not_undo_a_completed_registration(monkeypatch):
    import ml.s3 as s3mod

    def boom(uid, b):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(s3mod, "save_employee_profile", boom)
    fs.set_backend(FakeFaceBackend())
    r = asyncio.run(faf.register_face("u1", "o1", _good_files()))
    assert r["success"] is True and r["code"] == "ok"


def test_check_face_registered_uses_the_service(monkeypatch):
    fs.set_backend(FakeFaceBackend(registered=True))
    assert asyncio.run(faf.check_face_registered("u1")) == {"registered": True}
    fs.set_backend(FakeFaceBackend(registered=False))
    assert asyncio.run(faf.check_face_registered("u1")) == {"registered": False}

    class Broken(FakeFaceBackend):
        def is_registered(self, user_id):
            raise RuntimeError("db down")

    fs.set_backend(Broken())
    with pytest.raises(Exception) as e:
        asyncio.run(faf.check_face_registered("u1"))
    assert getattr(e.value, "status_code", None) == 500 and "db down" not in str(e.value.detail)


# --------------------------------------------------------------------------- metrics + attempt log
def test_metrics_line_is_structured_json(db, monkeypatch):
    lines = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = H()
    fm.logger.addHandler(h)
    try:
        assert fm.logger.level == logging.INFO and fm.logger.propagate is False
        punch(force=False)
    finally:
        fm.logger.removeHandler(h)
    row = json.loads(lines[-1])
    assert row["action"] == "punch_in" and row["outcome"] == "ok" and row["forced"] is False
    assert row["score"] == 0.81 and row["img_w"] == 640 and row["img_h"] == 480 and row["img_bytes"] == 3
    assert row["brightness"] == 120.0 and row["blur"] == 90.0
    assert {"detect_ms", "match_ms", "total_ms"} <= set(row)
    assert row["user"] == fm.short_hash("u1") and "u1" not in json.dumps(row)


def test_attempt_log_failure_is_swallowed(db, monkeypatch):
    import psycopg2
    import core_functions.db_function as dbf
    monkeypatch.setattr(dbf, "get_db_config", lambda: {})
    row = {"action": "punch_in", "outcome": "ok", "score": 0.8, "forced": False, "total_ms": 5}

    def connect_fail(*a, **k):
        raise psycopg2.OperationalError("db down")

    monkeypatch.setattr(psycopg2, "connect", connect_fail)
    assert fm.persist_attempt("o1", "u1", row) is False

    class BadConn:
        closed = False
        def cursor(self):
            raise RuntimeError("cursor boom")
        def close(self):
            BadConn.closed = True

    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: BadConn())
    assert fm.persist_attempt("o1", "u1", row) is False and BadConn.closed

    # end to end: enabled, connection fails, the punch still succeeds
    monkeypatch.setenv("FACE_ATTEMPT_LOG", "1")
    monkeypatch.setattr(psycopg2, "connect", connect_fail)

    async def run():
        res = await faf.punch_with_face("u1", "o1", FakeUpload(b"i"), "punch_in", None, None, False)
        await asyncio.sleep(0.1)
        return res

    assert asyncio.run(run())["success"] is True

    # disabled: no connection attempt at all
    monkeypatch.setenv("FACE_ATTEMPT_LOG", "0")
    called = []
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: called.append(1))
    punch()
    time.sleep(0.05)
    assert not called
