"""Upload limits and type sniffing (WP12 K2): utils/uploads.py and its use by every UploadFile endpoint.
DB-free; uploads are in-memory Starlette UploadFile objects. Run: python -m pytest tests/test_uploads.py -q
"""
import asyncio
import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import uploads as U  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
EXE = b"MZ\x90\x00" + b"\x00" * 64
HTML = b"<html><script>alert(1)</script></html>"


def _mk(data, name):
    return UploadFile(file=io.BytesIO(data), filename=name)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("UPLOAD_MAX_MB", raising=False)
    monkeypatch.delenv("CHAT_UPLOAD_MAX_MB", raising=False)


# ---------------------------------------------------------------------------------------------------- sniffing
@pytest.mark.parametrize("data, kind", [(PNG, "png"), (JPEG, "jpeg"), (GIF, "gif"), (WEBP, "webp"), (PDF, "pdf"),
                                        (EXE, None), (HTML, None), (b"", None), (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
                                        (b"GIF8", None), (b"\x89PNG", None)])
def test_sniff_type(data, kind):
    assert U.sniff_type(data[:16]) == kind


@pytest.mark.parametrize("data", [PNG, JPEG, GIF, WEBP])
def test_images_are_accepted_by_default(data):
    assert run(U.read_upload(_mk(data, "x"))) == data


def test_pdf_needs_the_pdf_list():
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(PDF, "x")))
    assert e.value.status_code == 415
    assert run(U.read_upload(_mk(PDF, "x"), allowed_types=U.IMAGES_AND_PDF)) == PDF


@pytest.mark.parametrize("data", [EXE, HTML, b"just text"])
def test_other_content_is_415_whatever_the_name_or_header_says(data):
    f = UploadFile(file=io.BytesIO(data), filename="photo.png", headers=None)
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(f, 5, U.IMAGES))
    assert e.value.status_code == 415
    assert "Unsupported file type" in e.value.detail


def test_none_allowed_types_means_any_content_with_only_the_size_limit():
    assert run(U.read_upload(_mk(EXE, "doc.docx"), 1, None)) == EXE


# ---------------------------------------------------------------------------------------------------- size
def test_over_the_limit_is_413_and_reading_stops_early():
    class Counting(io.BytesIO):
        reads = 0

        def read(self, n=-1):
            Counting.reads += 1
            return super().read(n)
    big = PNG + b"\x00" * (3 * 1024 * 1024)
    f = UploadFile(file=Counting(big), filename="x.png")
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(f, 1, U.IMAGES, chunk_size=64 * 1024))
    assert e.value.status_code == 413 and e.value.detail == "File is too large (maximum 1 MB)"
    assert Counting.reads <= 18          # 1 MB / 64 KB = 16 chunks, plus the one that crossed the limit; never the full 3 MB


def test_the_declared_size_is_checked_before_reading():
    f = UploadFile(file=io.BytesIO(PNG), filename="x.png", size=50 * 1024 * 1024)
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(f, 10, U.IMAGES))
    assert e.value.status_code == 413


def test_exactly_at_the_limit_is_accepted_and_one_byte_more_is_not():
    limit = 1024 * 1024
    ok = PNG + b"\x00" * (limit - len(PNG))
    assert len(run(U.read_upload(_mk(ok, "x"), 1, U.IMAGES))) == limit
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(ok + b"\x00", "x"), 1, U.IMAGES))
    assert e.value.status_code == 413


def test_empty_file():
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(b"", "x")))
    assert e.value.status_code == 400
    assert run(U.read_upload(_mk(b"", "x"), allow_empty=True)) == b""


def test_multi_chunk_files_are_reassembled():
    data = PNG + bytes(range(256)) * 1000
    assert run(U.read_upload(_mk(data, "x"), 5, U.IMAGES, chunk_size=1000)) == data


def test_a_wrong_type_is_refused_after_the_first_chunk():
    class Counting(io.BytesIO):
        reads = 0

        def read(self, n=-1):
            Counting.reads += 1
            return super().read(n)
    f = UploadFile(file=Counting(EXE + b"\x00" * (2 * 1024 * 1024)), filename="x.png")
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(f, 10, U.IMAGES, chunk_size=64 * 1024))
    assert e.value.status_code == 415 and Counting.reads <= 2


# ---------------------------------------------------------------------------------------------------- env
def test_env_defaults_and_overrides(monkeypatch):
    assert U.upload_max_mb() == 10 and U.chat_upload_max_mb() == 25
    monkeypatch.setenv("UPLOAD_MAX_MB", "2.5")
    monkeypatch.setenv("CHAT_UPLOAD_MAX_MB", "50")
    assert U.upload_max_mb() == 2.5 and U.chat_upload_max_mb() == 50
    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("UPLOAD_MAX_MB", bad)
        assert U.upload_max_mb() == 10


def test_the_env_limit_is_the_default_of_read_upload(monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_MB", "1")
    big = PNG + b"\x00" * (1024 * 1024)
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(big, "x")))
    assert e.value.status_code == 413


def test_check_upload_rewinds_the_file():
    f = _mk(PNG, "x")
    assert run(U.check_upload(f)) == len(PNG)
    assert run(f.read()) == PNG                      # the endpoint's own code can still read it


# ---------------------------------------------------------------------------------------------------- the endpoints
def _client(*modules):
    app = FastAPI()
    for m in modules:
        app.include_router(m.router)
    return TestClient(app)


def test_org_logo_and_employee_profile_endpoints(monkeypatch):
    import router.auth as ra
    saved = {}
    monkeypatch.setattr(ra, "save_org_logo", lambda org, data: saved.setdefault("logo", data) and "https://s/logo")
    monkeypatch.setattr(ra, "save_employee_profile", lambda uid, data: saved.setdefault("photo", data) and "https://s/p")
    c = _client(ra)
    r = c.post("/auth/upload-org-logo/o1", files={"logo": ("l.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json() == {"success": True, "logo_url": "https://s/logo"}
    r = c.post("/auth/upload-employee-profile/u1", files={"photo": ("p.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200 and r.json() == {"success": True, "photo_url": "https://s/p"}
    r = c.post("/auth/upload-employee-profile/u1", files={"photo": ("p.jpg", EXE, "image/jpeg")})
    assert r.status_code == 415 and "photo" in saved
    r = c.post("/auth/upload-org-logo/o1", files={"logo": ("l.png", PDF, "image/png")})
    assert r.status_code == 415


def test_oversize_profile_photo_is_413(monkeypatch):
    import router.profile_photo as pp
    monkeypatch.setenv("UPLOAD_MAX_MB", "1")
    monkeypatch.setattr(pp, "save_employee_profile", lambda *a: "https://s/p")
    c = _client(pp)
    ok = c.post("/profile-photo/u1", files={"photo": ("p.png", PNG, "image/png")})
    assert ok.status_code == 200 and ok.json() == {"success": True, "photo_url": "https://s/p"}
    big = PNG + b"\x00" * (1024 * 1024 + 10)
    r = c.post("/profile-photo/u1", files={"photo": ("p.png", big, "image/png")})
    assert r.status_code == 413 and r.json() == {"detail": "File is too large (maximum 1 MB)"}


def test_company_logo_endpoint(monkeypatch):
    import router.company as co
    monkeypatch.setattr(co, "save_org_logo", lambda org, data: "https://s/logo")
    c = _client(co)
    r = c.post("/company/update-logo/o1", files={"logo": ("l.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["logo_url"] == "https://s/logo"
    assert c.post("/company/update-logo/o1", files={"logo": ("l.png", HTML, "image/png")}).status_code == 415


def test_support_photo_is_checked_but_stays_best_effort(monkeypatch):
    import router.support as sp
    created = []

    async def submit(user_id, org_id, subject, message, photo_key):
        created.append(photo_key)
        return {"request_id": 1}
    monkeypatch.setattr(sp, "submit_support_request", submit)
    import ml.s3 as s3
    monkeypatch.setattr(s3, "upload_support_photo", lambda b, n, t: ("client_support/k", "https://s/k"))
    c = _client(sp)
    form = {"user_id": "u", "org_id": "o", "subject": "s", "message": "m"}
    r = c.post("/support/submit", data=form, files={"photo": ("a.png", PNG, "image/png")})
    assert r.status_code == 200 and created == ["client_support/k"]
    r = c.post("/support/submit", data=form)                                    # no photo: unchanged
    assert r.status_code == 200 and r.json() == {"success": True, "data": {"request_id": 1}}
    r = c.post("/support/submit", data=form, files={"photo": ("a.png", EXE, "image/png")})
    assert r.status_code == 415 and len(created) == 2                           # refused, no ticket created
    monkeypatch.setattr(s3, "upload_support_photo", lambda b, n, t: (_ for _ in ()).throw(RuntimeError("s3 down")))
    r = c.post("/support/submit", data=form, files={"photo": ("a.png", PNG, "image/png")})
    assert r.status_code == 200 and created[-1] is None                         # storage hiccup: ticket still created


def test_expense_receipt_endpoints_accept_images_and_pdf_receipts(monkeypatch):
    import router.emp_expenses as ex
    monkeypatch.setattr(ex, "save_odometer_photo", lambda *a: "https://s/odo", raising=False)
    monkeypatch.setattr(ex, "save_travel_bill_photo", lambda *a: "https://s/bill", raising=False)

    class Conn:
        def cursor(self, cursor_factory=None):
            return self

        def execute(self, *a):
            pass

        def fetchone(self):
            return {"parent_id": "org-1"}

        def close(self):
            pass
    import core_functions.db_function as db
    monkeypatch.setattr(db, "get_db_connection", lambda: Conn())
    monkeypatch.setattr(db, "return_connection", lambda c: None)
    c = _client(ex)
    q = {"user_id": "u", "reading_date": "2026-01-01", "photo_type": "start"}
    assert c.post("/expenses/odometer/upload-photo", params=q, files={"file": ("a.jpg", JPEG, "image/jpeg")}).json() == \
        {"success": True, "url": "https://s/odo"}
    assert c.post("/expenses/odometer/upload-photo", params=q, files={"file": ("a.pdf", PDF, "application/pdf")}).status_code == 415
    b = {"user_id": "u", "expense_id": "e"}
    assert c.post("/expenses/travel-bill/upload-photo", params=b, files={"file": ("a.pdf", PDF, "application/pdf")}).json() == \
        {"success": True, "url": "https://s/bill"}
    assert c.post("/expenses/travel-bill/upload-photo", params=b, files={"file": ("a.exe", EXE, "image/png")}).status_code == 415


def test_chat_attachments_use_the_chat_limit_and_any_type(monkeypatch):
    src = (ROOT / "router" / "chat.py").read_text(encoding="utf-8-sig")
    assert src.count("await read_upload(file, chat_upload_max_mb(), None)") == 2     # direct and group attachments
    assert "await file.read()" not in src


def test_face_endpoints_validate_every_photo():
    src = (ROOT / "router" / "face_attendance.py").read_text(encoding="utf-8-sig")
    assert "await check_upload(face_file, allowed_types=IMAGES)" in src
    assert "await check_upload(face, allowed_types=IMAGES)" in src


def test_no_endpoint_reads_an_upload_unbounded():
    """Every `UploadFile` read in router/ goes through utils.uploads (the face functions get a validated, rewound file)."""
    offenders = []
    for path in (ROOT / "router").glob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        if "UploadFile" in text and any(tok in text for tok in (".read()",)):
            offenders.append(path.name)
    assert offenders == []


def test_face_endpoints_refuse_bad_photos_before_any_processing(monkeypatch):
    """router/face_attendance.py with the ML layer stubbed (cv2 / insightface are not installed here): oversize and
    non-image photos are refused with 413 / 415 before register_face / punch_with_face run; good photos pass and the
    function still reads the full file afterwards (the router rewinds it)."""
    import importlib
    import types
    seen = {}

    async def register_face(user_id, org_id, faces):
        seen["register"] = [await f.read() for f in faces]
        return {"success": True, "message": "registered"}

    async def punch_with_face(user_id, org_id, face, action, latitude=None, longitude=None, force_accept=False):
        seen["punch"] = await face.read()
        return {"success": True}

    async def anything(*a, **k):
        return {"success": True}
    fake = types.ModuleType("core_functions.face_attendance_functions")
    for name in ("check_face_registered", "check_punch_location", "get_today_attendance", "warmup_user",
                 "get_attendance_sessions"):
        setattr(fake, name, anything)
    fake.register_face, fake.punch_with_face = register_face, punch_with_face
    monkeypatch.setitem(sys.modules, "core_functions.face_attendance_functions", fake)
    sys.modules.pop("router.face_attendance", None)
    try:
        mod = importlib.import_module("router.face_attendance")
        monkeypatch.setattr(mod, "check_plan_feature", anything)
        monkeypatch.setenv("UPLOAD_MAX_MB", "1")
        c = _client(mod)
        form = {"user_id": "u", "org_id": "o"}
        good = [("faces", ("a%d.jpg" % i, JPEG, "image/jpeg")) for i in range(4)]
        r = c.post("/face/register", data=form, files=good)
        assert r.status_code == 200 and r.json() == {"success": True, "message": "registered"}
        assert seen["register"] == [JPEG] * 4
        bad = good[:3] + [("faces", ("x.jpg", EXE, "image/jpeg"))]
        assert c.post("/face/register", data=form, files=bad).status_code == 415
        big = good[:3] + [("faces", ("x.jpg", JPEG + b"\x00" * (1024 * 1024 + 1), "image/jpeg"))]
        assert c.post("/face/register", data=form, files=big).status_code == 413
        punch = {**form, "action": "in"}
        assert c.post("/face/punch", data=punch, files={"face": ("f.jpg", JPEG, "image/jpeg")}).status_code == 200
        assert seen["punch"] == JPEG
        seen.pop("punch")
        assert c.post("/face/punch", data=punch, files={"face": ("f.jpg", HTML, "image/jpeg")}).status_code == 415
        assert "punch" not in seen
    finally:
        sys.modules.pop("router.face_attendance", None)


# ---------------------------------------------------------------------------------------------------- HEIC / HEIF
def _ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00" + b"\x00" * 64


HEIC = _ftyp(b"heic")
MP4 = _ftyp(b"mp42")


@pytest.mark.parametrize("brand", [b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"])
def test_sniff_recognises_every_heic_brand(brand):
    assert U.sniff_type(_ftyp(brand)[:16]) == "heic" and U.is_heic(_ftyp(brand))


@pytest.mark.parametrize("brand", [b"mp42", b"isom", b"qt  ", b"M4A ", b"avif"])
def test_sniff_refuses_non_image_ftyp_brands(brand):
    assert U.sniff_type(_ftyp(brand)[:16]) is None and not U.is_heic(_ftyp(brand))


def test_heic_is_refused_unless_allow_heic():
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(HEIC, "x.heic")))
    assert e.value.status_code == 415
    assert run(U.read_upload(_mk(HEIC, "x.heic"), allow_heic=True)) == HEIC
    assert run(U.read_upload(_mk(HEIC, "x"), allowed_types=U.IMAGES_AND_PDF, allow_heic=True)) == HEIC
    assert run(U.read_upload(_mk(PNG, "x"), allow_heic=True)) == PNG          # the normal kinds still pass


def test_fake_ftyp_brand_is_415_even_with_allow_heic():
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(MP4, "x.heic"), allow_heic=True))
    assert e.value.status_code == 415 and "heic" in e.value.detail


def test_heic_size_limits_are_unchanged():
    limit = 1024 * 1024
    ok = HEIC + b"\x00" * (limit - len(HEIC))
    assert len(run(U.read_upload(_mk(ok, "x"), 1, U.IMAGES, allow_heic=True))) == limit
    with pytest.raises(HTTPException) as e:
        run(U.read_upload(_mk(ok + b"\x00", "x"), 1, U.IMAGES, allow_heic=True))
    assert e.value.status_code == 413


def test_check_upload_heic_flag_and_rewind():
    with pytest.raises(HTTPException) as e:
        run(U.check_upload(_mk(HEIC, "x"), allowed_types=U.IMAGES))
    assert e.value.status_code == 415
    f = _mk(HEIC, "x")
    assert run(U.check_upload(f, allowed_types=U.IMAGES, allow_heic=True)) == len(HEIC)
    assert run(f.read()) == HEIC


def test_generic_image_endpoints_accept_heic_and_refuse_a_fake_brand(monkeypatch):
    import router.auth as ra
    import router.company as co
    import router.profile_photo as pp
    saved = []
    monkeypatch.setattr(ra, "save_org_logo", lambda org, data: saved.append(("logo", data)) or "https://s/logo")
    monkeypatch.setattr(ra, "save_employee_profile", lambda uid, data: saved.append(("photo", data)) or "https://s/p")
    monkeypatch.setattr(co, "save_org_logo", lambda org, data: saved.append(("logo2", data)) or "https://s/logo")
    monkeypatch.setattr(pp, "save_employee_profile", lambda uid, data: saved.append(("pp", data)) or "https://s/p")
    c = _client(ra, co, pp)
    assert c.post("/auth/upload-org-logo/o1", files={"logo": ("l.heic", HEIC, "image/heic")}).status_code == 200
    assert c.post("/auth/upload-employee-profile/u1", files={"photo": ("p.heic", HEIC, "image/heic")}).status_code == 200
    assert c.post("/company/update-logo/o1", files={"logo": ("l.heic", HEIC, "image/heic")}).status_code == 200
    assert c.post("/profile-photo/u1", files={"photo": ("p.heic", HEIC, "image/heic")}).status_code == 200
    assert [d for _, d in saved] == [HEIC] * 4
    assert c.post("/profile-photo/u1", files={"photo": ("p.heic", MP4, "image/heic")}).status_code == 415
    assert len(saved) == 4


def test_expense_receipts_accept_heic(monkeypatch):
    import router.emp_expenses as ex
    got = []
    monkeypatch.setattr(ex, "save_odometer_photo", lambda *a: got.append(a[-1]) or "k1", raising=False)
    monkeypatch.setattr(ex, "save_travel_bill_photo", lambda *a: got.append(a[-1]) or "k2", raising=False)

    class Conn:
        def cursor(self, cursor_factory=None):
            return self

        def execute(self, *a):
            pass

        def fetchone(self):
            return {"parent_id": "org-1"}

        def close(self):
            pass
    import core_functions.db_function as db
    monkeypatch.setattr(db, "get_db_connection", lambda: Conn())
    monkeypatch.setattr(db, "return_connection", lambda c: None)
    c = _client(ex)
    q = {"user_id": "u", "reading_date": "2026-01-01", "photo_type": "start"}
    assert c.post("/expenses/odometer/upload-photo", params=q, files={"file": ("a.heic", HEIC, "image/heic")}).status_code == 200
    b = {"user_id": "u", "expense_id": "e"}
    assert c.post("/expenses/travel-bill/upload-photo", params=b, files={"file": ("a.heic", HEIC, "image/heic")}).status_code == 200
    assert c.post("/expenses/travel-bill/upload-photo", params=b, files={"file": ("a.heic", MP4, "image/heic")}).status_code == 415
    assert got == [HEIC, HEIC]


def test_face_endpoints_never_allow_heic():
    src = (ROOT / "router" / "face_attendance.py").read_text(encoding="utf-8-sig")
    assert "allow_heic" not in src
    # behaviour: the exact call the face routes make refuses HEIC
    with pytest.raises(HTTPException) as e:
        run(U.check_upload(_mk(HEIC, "f.heic"), allowed_types=U.IMAGES))
    assert e.value.status_code == 415


def test_chat_attachments_still_take_any_content_and_heic_is_labelled(monkeypatch):
    assert run(U.read_upload(_mk(HEIC, "x.heic"), 1, None)) == HEIC
    import ml.s3 as s3mod
    puts = []

    class FakeS3:
        def put_object(self, **kw):
            puts.append(kw)

        def generate_presigned_url(self, *a, **k):
            return "https://s/u"
    monkeypatch.setattr(s3mod, "s3", FakeS3())
    key, _ = s3mod.upload_chat_file(HEIC, "IMG_1.JPG", "image/jpeg")
    assert key.endswith("_IMG_1.heic") and puts[0]["ContentType"] == "image/heic"
    s3mod.upload_chat_file(PNG, "a.png", "image/png")
    assert puts[1]["ContentType"] == "image/png" and puts[1]["Key"].endswith("_a.png")


def test_storage_keeps_heic_as_received_under_a_heic_key(monkeypatch):
    import ml.s3 as s3mod
    ops = []

    class FakeS3:
        def put_object(self, **kw):
            ops.append(("put", kw["Key"], kw["ContentType"], kw["Body"]))

        def delete_object(self, **kw):
            ops.append(("delete", kw["Key"]))

        def head_object(self, **kw):
            if kw["Key"] not in {"employee_profile/u1.heic", "org_logo/o1.heic"}:
                raise RuntimeError("404")

        def generate_presigned_url(self, op, Params, ExpiresIn):
            return "https://s/" + Params["Key"]
    monkeypatch.setattr(s3mod, "s3", FakeS3())
    s3mod.save_employee_profile("u1", HEIC)
    assert ops == [("put", "employee_profile/u1.heic", "image/heic", HEIC), ("delete", "employee_profile/u1.jpg")]
    ops.clear()
    s3mod.save_employee_profile("u1", JPEG)                              # unchanged behaviour for JPEG
    assert ops == [("put", "employee_profile/u1.jpg", "image/jpeg", JPEG)]
    ops.clear()
    s3mod.save_org_logo("o1", HEIC)
    assert ops[0][1:3] == ("org_logo/o1.heic", "image/heic") and ops[1] == ("delete", "org_logo/o1.png")
    ops.clear()
    s3mod.save_org_logo("o1", PNG)
    assert ops == [("put", "org_logo/o1.png", "image/png", PNG)]
    assert s3mod.get_employee_profile_url("u1") == "https://s/employee_profile/u1.heic"
    assert s3mod.get_org_logo_url("o1") == "https://s/org_logo/o1.heic"
    assert s3mod.get_org_logo_url("nobody") is None


def test_expense_photo_storage_and_lookup_use_the_heic_key(monkeypatch):
    import core_functions.emp_expenses_functions as ef
    import ml.s3 as s3mod
    ops = []

    class FakeS3:
        def put_object(self, **kw):
            ops.append(("put", kw["Key"], kw["ContentType"]))

        def delete_object(self, **kw):
            ops.append(("delete", kw["Key"]))

        def head_object(self, **kw):
            if not kw["Key"].endswith("e1.heic"):
                raise RuntimeError("404")

        def generate_presigned_url(self, op, Params, ExpiresIn):
            return "https://s/" + Params["Key"]
    fake = FakeS3()
    monkeypatch.setattr(s3mod, "s3", fake)
    monkeypatch.setattr(ef, "s3", fake)
    assert ef.save_travel_bill_photo("o", "u", "e1", HEIC) == "travel_bill_photos/o/u/e1.heic"
    assert ops == [("put", "travel_bill_photos/o/u/e1.heic", "image/heic"), ("delete", "travel_bill_photos/o/u/e1.png")]
    ops.clear()
    assert ef.save_travel_bill_photo("o", "u", "e1", PDF) == "travel_bill_photos/o/u/e1.png"   # PDF/PNG: key unchanged
    assert ops == [("put", "travel_bill_photos/o/u/e1.png", "image/png")]
    assert ef.save_odometer_photo("o", "u", "2026-01-01", "start", HEIC) == "odo_meter_photos/o/u/2026-01-01_start.heic"
    assert ef.get_travel_bill_photo_url("o", "u", "e1") == "https://s/travel_bill_photos/o/u/e1.heic"
    assert ef.get_travel_bill_photo_url("o", "u", "zz") is None
