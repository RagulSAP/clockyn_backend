"""DB-free, AWS-free tests for ml/face_service.py (facade, Rekognition backend, mock backend, user_face_index store),
the anti-spoof crop and the scripts/rekognition_setup.py helpers. Run: python -m pytest tests/test_face_service.py -q
"""
import importlib.util
import io
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ml.face as mlface  # noqa: E402
import ml.face_service as fs  # noqa: E402
import ml.rekognition_face as rf  # noqa: E402
from face_fakes import FakeRekognitionClient, FakeStore, client_error, face_detail, jpeg_bytes, match_entry  # noqa: E402

USER = "3f2b8c1e-5d4a-4c8e-9b1f-0a1b2c3d4e5f"


def backend(threshold=90.0, client=None, store=None):
    client = client or FakeRekognitionClient()
    store = store or FakeStore()
    return fs.RekognitionBackend(rf.RekognitionFace(client=client, collection="clockyn-test", threshold=threshold), store), client, store


def photos(n=4):
    return [jpeg_bytes(seed=i + 1) for i in range(n)]


# --------------------------------------------------------------------------- register
def test_register_happy_path_stores_four_face_ids_and_replaces_the_old_faces():
    be, client, store = backend()
    client.faces = {"old-1": USER, "old-2": USER, "other-1": "someone-else"}
    store.seed(USER, ["old-1", "old-2"])
    store.seed("someone-else", ["other-1"])

    res = be.register(USER, photos(4))

    assert res.success and res.code == "ok" and res.face_ids == ["face-001", "face-002", "face-003", "face-004"]
    assert client.names().count("detect_faces") == 4 and client.names().count("index_faces") == 4
    assert all(kw["ExternalImageId"] == USER for kw in client.calls_of("index_faces"))
    assert [(u, f, n) for u, f, n in store.rows if u == USER] == [(USER, "face-%03d" % i, i) for i in range(1, 5)]
    assert set(client.faces) == {"face-001", "face-002", "face-003", "face-004", "other-1"}   # old faces gone, others untouched
    assert ("someone-else", "other-1", 1) in store.rows
    # the old enrolment is deleted only AFTER the new faces are indexed and their rows committed
    names = client.names()
    assert max(i for i, n in enumerate(names) if n == "index_faces") < names.index("delete_faces")
    assert store.events == ["list", "add", "delete"]
    assert client.calls_of("delete_faces")[0]["FaceIds"] == ["old-1", "old-2"]


def test_register_first_time_has_nothing_to_delete():
    be, client, store = backend()
    assert be.register(USER, photos(4)).success
    assert "delete_faces" not in client.names() and store.events == ["list", "add"]


def test_register_partial_failure_compensates_and_keeps_the_old_registration():
    be, client, store = backend()
    client.faces = {"old-1": USER}
    store.seed(USER, ["old-1"])
    client.script["detect_faces"] = [None, None, {"FaceDetails": []}]       # photo 3 has no face (None = default one face)
    res = be.register(USER, photos(4))
    assert (res.success, res.code, res.failed_index, res.reason) == (False, "no_face", 2, None)
    assert set(client.faces) == {"old-1"}                                   # the two new faces were deleted again
    assert store.ids_of(USER) == ["old-1"] and "add" not in store.events    # old rows untouched, no half registration


def test_register_index_filter_reason_is_reported_as_the_app_reason():
    be, client, store = backend()
    client.script["index_faces"] = [None, {"FaceRecords": [], "UnindexedFaces": [{"Reasons": ["LOW_BRIGHTNESS"]}]}]
    res = be.register(USER, photos(4))
    assert (res.code, res.reason, res.failed_index) == ("no_face", "too_dark", 1)
    assert client.faces == {} and store.rows == []                          # the first indexed face was compensated

    be, client, store = backend()
    client.script["index_faces"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    res = be.register(USER, photos(4))
    assert (res.code, res.reason, res.failed_index) == ("no_face", None, 0)


def test_register_multiple_faces_is_refused_with_its_own_reason():
    be, client, store = backend()
    client.details = [face_detail(0.1, 0.1, 0.4, 0.5), face_detail(0.55, 0.15, 0.35, 0.45)]
    res = be.register(USER, photos(4))
    assert (res.success, res.code, res.reason, res.failed_index) == (False, "no_face", "multiple_faces", 0)
    assert res.message == fs.MULTIPLE_FACES_MESSAGE and "index_faces" not in client.names()   # no paid IndexFaces call


def test_register_db_failure_deletes_the_new_faces_from_rekognition():
    be, client, store = backend()
    client.faces = {"old-1": USER}
    store.seed(USER, ["old-1"])
    store.fail_add = True
    res = be.register(USER, photos(4))
    assert (res.success, res.code) == (False, "server_error") and "db down" not in res.message
    assert set(client.faces) == {"old-1"} and store.ids_of(USER) == ["old-1"]


def test_register_compensation_failure_is_logged_with_the_face_ids_and_never_raises(caplog):
    be, client, store = backend()
    store.fail_add = True
    client.script["delete_faces"] = [client_error("ThrottlingException", "slow")]
    with caplog.at_level(logging.ERROR):
        res = be.register(USER, photos(4))
    assert res.code == "server_error"
    assert "face-001" in caplog.text and "face-004" in caplog.text           # an operator can delete them by FaceId


def test_register_throttled_mid_way_is_unavailable_and_compensates():
    be, client, store = backend()
    client.script["index_faces"] = [None, None, client_error("ProvisionedThroughputExceededException", "slow")]
    res = be.register(USER, photos(4))
    assert (res.success, res.code, res.message) == (False, "unavailable", fs.BUSY_MESSAGE)
    assert client.faces == {} and store.rows == []


def test_register_old_face_cleanup_failure_keeps_rows_for_a_retry_and_still_succeeds():
    be, client, store = backend()
    client.faces = {"old-1": USER}
    store.seed(USER, ["old-1"])
    client.script["delete_faces"] = [client_error("InternalServerError", "oops")]
    res = be.register(USER, photos(4))
    assert res.success and "old-1" in store.ids_of(USER)                    # rows kept: the next register / remove retries
    assert be.remove(USER).ok is True and store.ids_of(USER) == [] and client.faces == {}


def test_register_unreadable_photo_and_empty_input():
    be, client, store = backend()
    res = be.register(USER, [b"not an image"] * 4)
    assert (res.success, res.code, res.failed_index) == (False, "image_unreadable", 0) and client.calls == []
    assert be.register(USER, []).success is False


# --------------------------------------------------------------------------- verify
def test_verify_match_and_no_match_and_the_threshold_boundary():
    be, client, _ = backend(threshold=90.0)
    client.matches = [match_entry(USER, 99.2)]
    res = be.verify(USER, jpeg_bytes())
    assert res.match and res.code == "ok" and res.similarity == 99.2 and res.score == 0.992 and res.face_found
    assert res.box == (0.3, 0.2, 0.4, 0.5)

    client.matches = []
    res = be.verify(USER, jpeg_bytes())
    assert (res.match, res.code, res.similarity, res.score, res.message) == (False, "no_match", None, None, "Face not recognized")

    client.matches = [match_entry(USER, 89.99)]                              # just below the threshold
    res = be.verify(USER, jpeg_bytes())
    assert res.match is False and res.code == "no_match" and res.similarity == 89.99
    client.matches = [match_entry(USER, 90.0)]                               # exactly the threshold matches
    assert be.verify(USER, jpeg_bytes()).match is True
    client.matches = [match_entry(USER, 90.01)]
    assert be.verify(USER, jpeg_bytes()).match is True


def test_verify_only_counts_the_claimed_users_faces():
    be, client, _ = backend()
    client.matches = [match_entry("someone-else", 99.9), match_entry(USER, 91.0, "mine")]
    res = be.verify(USER, jpeg_bytes())
    assert res.match and res.similarity == 91.0
    client.matches = [match_entry("someone-else", 99.9)]                     # another user's face only: never a match
    res = be.verify(USER, jpeg_bytes())
    assert res.match is False and res.code == "no_match" and res.similarity == 99.9
    client.matches = [match_entry("???:zz", 99.9)]                           # a face this app did not write
    assert be.verify(USER, jpeg_bytes()).match is False


def test_verify_threshold_comes_from_the_environment_and_is_sent_to_rekognition(monkeypatch):
    monkeypatch.setenv("REKOGNITION_THRESHOLD", "95")
    client = FakeRekognitionClient()
    be = fs.RekognitionBackend(rf.RekognitionFace(client=client, collection="c"), FakeStore())
    client.matches = [match_entry(USER, 94.0)]
    assert be.verify(USER, jpeg_bytes()).match is False
    client.matches = [match_entry(USER, 95.5)]
    assert be.verify(USER, jpeg_bytes()).match is True
    assert client.calls_of("search_faces_by_image")[0]["FaceMatchThreshold"] == 95.0


def test_verify_no_face_and_multiple_faces_never_reach_the_paid_search():
    be, client, _ = backend()
    client.details = []
    res = be.verify(USER, jpeg_bytes())
    assert (res.match, res.face_found, res.code, res.reason) == (False, False, "no_face", None)
    client.details = [face_detail(0.1, 0.1, 0.4, 0.5), face_detail(0.55, 0.15, 0.35, 0.45)]
    res = be.verify(USER, jpeg_bytes())
    assert (res.code, res.reason, res.face_found) == ("no_face", "multiple_faces", True) and res.message == fs.MULTIPLE_FACES_MESSAGE
    assert "search_faces_by_image" not in client.names()
    # a far-away passer-by does not block the punch
    client.details = [face_detail(0.3, 0.2, 0.4, 0.5), face_detail(0.9, 0.9, 0.04, 0.05)]
    client.matches = [match_entry(USER, 97.0)]
    assert be.verify(USER, jpeg_bytes()).match is True


def test_verify_search_without_a_usable_face_is_no_face():
    be, client, _ = backend()
    client.script["search_faces_by_image"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    res = be.verify(USER, jpeg_bytes())
    assert (res.code, res.face_found) == ("no_face", False) and res.message == fs.NO_FACE_MESSAGE


def test_verify_spoof_gate_runs_before_the_search_with_the_bounding_box():
    be, client, _ = backend()
    client.matches = [match_entry(USER, 99.0)]
    seen = []
    res = be.verify(USER, jpeg_bytes(), spoof_check=lambda box: seen.append(box) or False)
    assert (res.match, res.code) == (False, "spoof_detected") and seen == [(0.3, 0.2, 0.4, 0.5)]
    assert "search_faces_by_image" not in client.names()
    assert be.verify(USER, jpeg_bytes(), spoof_check=lambda box: True).match is True


def test_verify_big_photo_is_re_encoded_below_5mb_before_sending():
    be, client, _ = backend()
    client.matches = [match_entry(USER, 99.0)]
    buf = io.BytesIO()
    rng = np.random.default_rng(5)
    Image.fromarray(rng.integers(0, 255, (2600, 3000, 3), dtype=np.uint8)).save(buf, "PNG")
    assert len(buf.getvalue()) > rf.MAX_IMAGE_BYTES
    assert be.verify(USER, buf.getvalue()).match is True
    sent = client.calls_of("detect_faces")[0]["Image"]["Bytes"]
    assert len(sent) <= rf.MAX_IMAGE_BYTES and max(Image.open(io.BytesIO(sent)).size) <= rf.MAX_IMAGE_SIDE
    assert client.calls_of("search_faces_by_image")[0]["Image"]["Bytes"] == sent        # the same fitted bytes are searched


@pytest.mark.parametrize("code,expected", [
    ("ThrottlingException", "unavailable"),
    ("ProvisionedThroughputExceededException", "unavailable"),
    ("InternalServerError", "unavailable"),
    ("AccessDeniedException", "server_error"),
    ("ResourceNotFoundException", "server_error"),
    ("InvalidImageFormatException", "image_unreadable"),
])
def test_verify_service_errors_map_to_the_app_codes(code, expected):
    be, client, _ = backend()
    client.script["search_faces_by_image"] = [client_error(code, "x")]
    res = be.verify(USER, jpeg_bytes())
    assert res.match is False and res.code == expected
    if expected == "unavailable":
        assert res.message == fs.BUSY_MESSAGE
    if expected == "server_error":
        assert res.message == fs.GENERIC_MESSAGE


def test_verify_access_denied_and_missing_collection_log_the_actionable_cause(caplog):
    be, client, _ = backend()
    client.script["search_faces_by_image"] = [client_error("AccessDeniedException", "x")]
    with caplog.at_level(logging.ERROR):
        be.verify(USER, jpeg_bytes())
    assert "rekognition:SearchFacesByImage" in caplog.text
    caplog.clear()
    client.collection_exists = False
    with caplog.at_level(logging.ERROR):
        res = be.verify(USER, jpeg_bytes())
    assert res.code == "server_error" and "rekognition_setup.py create" in caplog.text


def test_verify_collection_missing_with_auto_create_recovers(monkeypatch):
    monkeypatch.setenv("REKOGNITION_AUTO_CREATE", "1")
    be, client, _ = backend()
    client.collection_exists = False
    res = be.verify(USER, jpeg_bytes())
    assert res.code == "no_match" and "create_collection" in client.names()     # created, search answered (empty)


def test_unreadable_photo_is_image_unreadable():
    be, client, _ = backend()
    res = be.verify(USER, b"garbage")
    assert (res.match, res.code, res.message) == (False, "image_unreadable", fs.UNREADABLE_MESSAGE) and client.calls == []


# --------------------------------------------------------------------------- identify / remove / state
def test_identify_returns_the_best_enrolled_user_at_or_above_the_threshold():
    be, client, _ = backend()
    client.matches = [match_entry("???:zz", 99.9), match_entry(USER, 96.0), match_entry("u2", 92.0)]
    assert be.identify(jpeg_bytes()) == (USER, 96.0)                         # foreign faces are skipped
    client.matches = [match_entry("u2", 89.9)]
    assert be.identify(jpeg_bytes()) is None
    client.matches = []
    assert be.identify(jpeg_bytes()) is None
    client.details = []
    assert be.identify(jpeg_bytes()) is None
    client.details = [face_detail(0.1, 0.1, 0.4, 0.5), face_detail(0.55, 0.15, 0.35, 0.45)]
    client.matches = [match_entry(USER, 99.0)]
    assert be.identify(jpeg_bytes()) is None                                 # several people: no identification
    client.details = [face_detail()]
    client.script["search_faces_by_image"] = [client_error("ThrottlingException", "x")]
    with pytest.raises(rf.RekognitionError):
        be.identify(jpeg_bytes())


def test_remove_deletes_faces_then_rows_and_reports_failures():
    be, client, store = backend()
    client.faces = {"a": USER, "b": USER, "c": "other"}
    store.seed(USER, ["a", "b"])
    store.seed("other", ["c"])
    res = be.remove(USER)
    assert (res.ok, res.removed, res.errors) == (True, 2, []) and set(client.faces) == {"c"}
    assert store.ids_of(USER) == [] and store.ids_of("other") == ["c"]
    assert be.remove(USER).ok is True and be.remove("never-registered").removed == 0

    client.faces = {"d": USER}
    store.seed(USER, ["d"])
    client.script["delete_faces"] = [client_error("InternalServerError", "x")]
    res = be.remove(USER)
    assert res.ok is False and res.errors and store.ids_of(USER) == ["d"]    # rows kept so the cleanup can be retried
    store.fail_list = True
    res = be.remove(USER)
    assert res.ok is False and "db down" in res.errors[0]


def test_is_registered_means_rows_exist_and_warmup_is_a_noop():
    be, client, store = backend()
    assert be.is_registered(USER) is False
    store.seed(USER, ["a"])
    assert be.is_registered(USER) is True
    assert be.warmup() is True and client.calls == []


# --------------------------------------------------------------------------- facade + backend selection
def test_facade_delegates_to_the_selected_backend_and_validates_the_name(monkeypatch):
    monkeypatch.setenv("FACE_BACKEND", "nonsense")
    fs.set_backend(None)
    with pytest.raises(ValueError):
        fs.get_backend()
    assert fs.warmup() is False                                              # never raises
    monkeypatch.delenv("FACE_BACKEND")
    assert isinstance(fs.get_backend(), fs.RekognitionBackend)               # default; constructing it makes no AWS / DB call
    monkeypatch.setenv("FACE_BACKEND", "MOCK")
    assert isinstance(fs.get_backend(), fs.MockBackend) and fs.backend_name() == "mock"
    be, client, store = backend()
    fs.set_backend(be)
    client.matches = [match_entry(USER, 99.0)]
    assert fs.register(USER, photos(4)).success and fs.is_registered(USER)
    assert fs.verify(USER, jpeg_bytes()).match and fs.identify(jpeg_bytes()) == (USER, 99.0)
    assert fs.remove(USER).ok and not fs.is_registered(USER)
    assert fs.warmup() is True


def test_deprecated_cosine_threshold_env_logs_a_warning_and_is_ignored(monkeypatch, caplog):
    monkeypatch.delenv("FACE_SIMILARITY_THRESHOLD", raising=False)
    with caplog.at_level(logging.WARNING):
        fs.warmup()
    assert "FACE_SIMILARITY_THRESHOLD" not in caplog.text
    monkeypatch.setenv("FACE_SIMILARITY_THRESHOLD", "0.62")
    monkeypatch.delenv("REKOGNITION_THRESHOLD", raising=False)
    with caplog.at_level(logging.WARNING):
        fs.warmup()
    assert "FACE_SIMILARITY_THRESHOLD" in caplog.text and "REKOGNITION_THRESHOLD" in caplog.text
    assert rf.match_threshold() == 90.0                                      # the old value does not change the threshold


# --------------------------------------------------------------------------- mock backend
def padded(seed, tail):
    """A decodable JPEG, zero padded past 64 KB, followed by `tail` (the mock hashes only the first 64 KB)."""
    base = jpeg_bytes(seed)
    return base + b"\x00" * (100_000 - len(base)) + tail


def test_mock_backend_matches_enrolled_photos_by_the_hash_of_the_first_64kb():
    be = fs.get_backend()
    assert isinstance(be, fs.MockBackend)
    a, b = padded(1, b"A" * 1000), padded(1, b"B" * 1000)
    other = padded(2, b"")
    assert a != b and Image.open(io.BytesIO(a)).size == (320, 320)
    res = fs.register(USER, [a, jpeg_bytes(7), jpeg_bytes(8), jpeg_bytes(9)])
    assert res.success and len(res.face_ids) == 4 and fs.is_registered(USER) and not fs.is_registered("nobody")
    assert fs.verify(USER, a).match is True and fs.verify(USER, a).similarity == 100.0
    assert fs.verify(USER, b).match is True                                  # same first 64 KB: same photo for the mock
    assert fs.verify(USER, jpeg_bytes(8)).match is True
    res = fs.verify(USER, other)
    assert (res.match, res.code, res.message) == (False, "no_match", "Face not recognized")
    assert fs.verify("nobody", a).match is False
    assert fs.identify(a) == (USER, 100.0) and fs.identify(other) is None
    assert fs.remove(USER).removed == 4 and not fs.is_registered(USER) and fs.verify(USER, a).match is False


def test_mock_backend_always_match_and_undecodable_images(monkeypatch):
    fs.register(USER, photos(4))
    assert fs.verify(USER, jpeg_bytes(99)).match is False
    monkeypatch.setenv("FACE_MOCK_ALWAYS_MATCH", "1")
    res = fs.verify(USER, jpeg_bytes(99))
    assert res.match is True and res.similarity == 95.0
    assert fs.identify(jpeg_bytes(99)) == (USER, 95.0)
    res = fs.verify(USER, b"garbage")                                        # not an image: no face, even when always matching
    assert (res.match, res.code, res.face_found) == (False, "no_face", False)
    assert fs.register(USER, [b"garbage"] * 4).code == "no_face"
    assert fs.register(USER, []).success is False
    res = fs.verify(USER, jpeg_bytes(1), spoof_check=lambda box: False)
    assert res.code == "spoof_detected" and res.box == fs.MOCK_FACE_BOX


@pytest.mark.parametrize("env", ["production", "PROD", " Production "])
def test_mock_backend_refuses_to_run_in_production(monkeypatch, env):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("FACE_BACKEND", "mock")
    fs.set_backend(None)
    with pytest.raises(RuntimeError) as e:
        fs.get_backend()
    assert "APP_ENV=production" in str(e.value)
    assert fs.warmup() is False                                              # start-up hook: logged, never raises
    monkeypatch.setenv("APP_ENV", "development")
    assert isinstance(fs.get_backend(), fs.MockBackend)


# --------------------------------------------------------------------------- logging hygiene
def test_facade_logs_neither_image_bytes_nor_raw_user_ids(caplog):
    caplog.set_level(logging.DEBUG)
    be, client, store = backend()
    client.matches = [match_entry("u-secret-id", 97.5)]
    user = "u-secret-id"
    be.register(user, photos(4))
    be.verify(user, jpeg_bytes(11))
    client.script["search_faces_by_image"] = [client_error("ThrottlingException", "x")]
    be.verify(user, jpeg_bytes(11))
    store.fail_add = True
    be.register(user, photos(4))
    be.remove(user)
    text = caplog.text
    photo = jpeg_bytes(11)
    assert "\\xff\\xd8" not in text and photo[:24].hex() not in text and repr(photo[:24]) not in text
    assert "u-secret-id" not in text and fs.uid_hash(user) in text           # only the hash of the user id
    assert "97.5" in text                                                    # similarities are logged


# --------------------------------------------------------------------------- user_face_index SQL (fake connection)
class FakeCursor:
    def __init__(self, log, rows=None, rowcount=0):
        self.log, self.rows, self.rowcount = log, rows or [], rowcount

    def execute(self, sql, params=None):
        self.log.append(("sql", " ".join(sql.split()), params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.log.append(("cursor_closed",))


class FakeConn:
    def __init__(self, cursor, log):
        self._cursor, self.log = cursor, log

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))


@pytest.fixture
def pg(monkeypatch):
    from core_functions import db_function as dbf
    log = []
    state = {"rows": [], "rowcount": 0, "fail_on": None}

    class Cur(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if state["fail_on"] and state["fail_on"] in sql:
                raise RuntimeError("boom")

    cur = Cur(log)
    conn = FakeConn(cur, log)

    def get_conn():
        cur.rows, cur.rowcount = state["rows"], state["rowcount"]
        return conn

    monkeypatch.setattr(dbf, "get_db_connection", get_conn)
    monkeypatch.setattr(dbf, "return_connection", lambda c: log.append(("returned",)))
    return log, state


def sqls(log):
    return [(e[1], e[2]) for e in log if e[0] == "sql"]


def test_store_reads_use_one_scoped_statement_and_return_the_connection(pg):
    log, state = pg
    store = fs.DbFaceStore()
    state["rows"] = [{"face_id": "f1"}, {"face_id": "f2"}]
    assert store.list_face_ids(USER) == ["f1", "f2"]
    assert sqls(log) == [("SELECT face_id FROM user_face_index WHERE user_id = %s ORDER BY id", (USER,))]
    assert ("returned",) in log and ("commit",) not in log
    state["rows"] = [{"registered": True}]
    assert store.has_faces(USER) is True
    assert sqls(log)[-1] == ("SELECT EXISTS (SELECT 1 FROM user_face_index WHERE user_id = %s) AS registered", (USER,))
    state["rows"] = [{"registered": False}]
    assert store.has_faces(USER) is False
    state["rows"] = []
    assert store.has_faces(USER) is False


def test_store_add_faces_is_one_transaction_and_rolls_back_on_error(pg):
    log, state = pg
    store = fs.DbFaceStore()
    store.add_faces(USER, [("f1", 1), ("f2", 2)])
    assert sqls(log) == [("INSERT INTO user_face_index (user_id, face_id, photo_no) VALUES (%s, %s, %s)", (USER, "f1", 1)),
                         ("INSERT INTO user_face_index (user_id, face_id, photo_no) VALUES (%s, %s, %s)", (USER, "f2", 2))]
    assert log.count(("commit",)) == 1 and ("rollback",) not in log and log[-1] == ("returned",)
    log.clear()
    state["fail_on"] = "INSERT"
    with pytest.raises(RuntimeError):
        store.add_faces(USER, [("f3", 3)])
    assert ("rollback",) in log and ("commit",) not in log and log[-1] == ("returned",)    # connection still given back


def test_store_delete_by_face_ids(pg):
    log, state = pg
    store = fs.DbFaceStore()
    state["rowcount"] = 2
    assert store.delete_face_ids(["f1", "f2", "", None]) == 2
    assert sqls(log) == [("DELETE FROM user_face_index WHERE face_id = ANY(%s)", (["f1", "f2"],))]
    assert log.count(("commit",)) == 1
    log.clear()
    assert store.delete_face_ids([]) == 0 and log == []                     # nothing to do: no connection used


# --------------------------------------------------------------------------- anti-spoof crop and check
def test_crop_face_converts_the_relative_box_to_pixels_with_a_margin():
    img = np.arange(100 * 200 * 3, dtype=np.uint32).reshape(100, 200, 3).astype(np.uint8)   # h=100, w=200
    crop = mlface.crop_face(img, (0.25, 0.25, 0.5, 0.5))
    # margin 10 % of the box on every side: x 0.2..0.8 -> 40..160, y 0.2..0.8 -> 20..80
    assert crop.shape == (60, 120, 3) and np.array_equal(crop, img[20:80, 40:160])
    assert mlface.crop_face(img, (0.25, 0.25, 0.5, 0.5), margin=0).shape == (50, 100, 3)
    edge = mlface.crop_face(img, (0.9, 0.9, 0.3, 0.3))                       # runs past the image: clamped
    assert edge.shape[0] <= 100 and edge.shape[1] <= 200 and edge.size > 0
    neg = mlface.crop_face(img, (-0.2, -0.2, 0.4, 0.4))
    assert neg.shape == (24, 48, 3)                                          # clamped at 0; far edge 0.24 of the image
    assert mlface.crop_face(img, (0.5, 0.5, 0.0, 0.0), margin=0).size == 0   # degenerate box -> empty
    assert mlface.crop_face(img, (1.5, 0.1, 0.2, 0.2)).size == 0             # entirely outside


@pytest.fixture
def fake_spoof(monkeypatch):
    """cv2 / onnxruntime are not installed here: a numpy / Pillow cv2 stand-in and a scripted model session."""
    import types
    cv2 = types.ModuleType("cv2")
    cv2.COLOR_BGR2RGB = 4
    cv2.resize = lambda a, size: np.array(Image.fromarray(a).resize(size))
    cv2.cvtColor = lambda a, code: a[..., ::-1].copy()
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    state = {"score": 0.2, "raise": False, "seen": None}

    class Session:
        def run(self, _names, feed):
            if state["raise"]:
                raise RuntimeError("onnx failed")
            state["seen"] = next(iter(feed.values()))
            return [np.array([[state["score"]]])]

    monkeypatch.setattr(mlface, "_spoof_session", lambda: (Session(), "input"))
    return state


def test_anti_spoof_check_keeps_the_old_pass_fail_rule(fake_spoof):
    crop = np.full((50, 40, 3), 120, dtype=np.uint8)
    fake_spoof["score"] = 0.2
    assert mlface.anti_spoof_check(crop) is True                             # lower score = real face
    assert fake_spoof["seen"].shape == (1, 3, 112, 112) and fake_spoof["seen"].dtype == np.float32
    fake_spoof["score"] = 0.5
    assert mlface.anti_spoof_check(crop) is False                            # not < 0.5
    fake_spoof["score"] = 0.9
    assert mlface.anti_spoof_check(crop) is False
    fake_spoof["raise"] = True
    assert mlface.anti_spoof_check(crop) is False                            # model error: fail closed, as before
    assert mlface.anti_spoof_check(None) is False and mlface.anti_spoof_check(crop[0:0]) is False


def test_verify_face_runs_the_spoof_check_on_the_rekognition_crop_when_enabled(monkeypatch):
    import core_functions.face_recognition as fr
    image = np.arange(100 * 200 * 3, dtype=np.uint32).reshape(100, 200, 3).astype(np.uint8)
    prepared = {"success": True, "jpeg": jpeg_bytes(3), "image": image, "quality": {}}
    fs.register(USER, [prepared["jpeg"]] * 4)
    crops = []

    def spoof(crop):
        crops.append(crop)
        return live["value"]

    live = {"value": True}
    monkeypatch.setattr(fr, "anti_spoof_check", spoof)

    monkeypatch.delenv("FACE_ANTISPOOF", raising=False)
    res = fr.verify_face(prepared, USER)
    assert res["match"] is True and res["score"] == 1.0 and crops == []      # off by default: no crop, no model

    monkeypatch.setenv("FACE_ANTISPOOF", "1")
    res = fr.verify_face(prepared, USER)
    assert res["match"] is True and len(crops) == 1
    assert np.array_equal(crops[0], mlface.crop_face(image, fs.MOCK_FACE_BOX))   # the crop of the service's box
    live["value"] = False
    res = fr.verify_face(prepared, USER)
    assert res["match"] is False and res["code"] == "spoof_detected" and res["score"] is None
    assert set(res) == {"match", "score", "code", "reason", "message", "face_found"}


# --------------------------------------------------------------------------- scripts/rekognition_setup.py
def load_setup():
    spec = importlib.util.spec_from_file_location("rekognition_setup", ROOT / "scripts" / "rekognition_setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_setup_policy_is_least_privilege(monkeypatch, capsys):
    setup = load_setup()
    monkeypatch.setattr(setup, "_account_id", lambda region: "123456789012")
    assert setup.main(["--region", "ap-south-1", "--collection", "clockyn-faces", "policy"]) == 0
    policy = json.loads(capsys.readouterr().out)
    arn = "arn:aws:rekognition:ap-south-1:123456789012:collection/clockyn-faces"
    assert policy["Version"] == "2012-10-17" and len(policy["Statement"]) == 2
    coll, detect = policy["Statement"]
    assert coll["Effect"] == "Allow" and coll["Resource"] == arn
    assert sorted(coll["Action"]) == sorted(["rekognition:IndexFaces", "rekognition:SearchFacesByImage", "rekognition:DeleteFaces",
                                             "rekognition:ListFaces", "rekognition:DescribeCollection"])
    assert detect["Action"] == ["rekognition:DetectFaces"] and detect["Resource"] == "*"
    everything = {a for st in policy["Statement"] for a in st["Action"]}
    assert not {"rekognition:CreateCollection", "rekognition:DeleteCollection", "rekognition:ListCollections"} & everything


def test_setup_policy_without_credentials_uses_the_placeholder_and_admin_snippet(monkeypatch, capsys):
    setup = load_setup()
    monkeypatch.setattr(setup, "_account_id", lambda region: None)
    assert setup.main(["policy"]) == 0
    out = capsys.readouterr()
    assert "<ACCOUNT_ID>" in out.out and json.loads(out.out)["Statement"][0]["Resource"].endswith(":collection/clockyn-faces")
    assert setup.main(["--collection", "my-faces", "policy", "--admin"]) == 0
    admin = json.loads(capsys.readouterr().out)
    actions = {a for st in admin["Statement"] for a in st["Action"]}
    assert {"rekognition:CreateCollection", "rekognition:ListCollections", "rekognition:DeleteCollection"} <= actions
    assert "rekognition:IndexFaces" in admin["Statement"][2]["Action"] and admin["Statement"][2]["Resource"].endswith("collection/clockyn-test-*")
    assert admin["Statement"][0]["Resource"].endswith("collection/my-faces")


def test_setup_tiny_png_is_a_valid_image():
    img = Image.open(io.BytesIO(load_setup().tiny_png()))
    img.load()
    assert img.size == (1, 1) and img.format == "PNG"


def test_setup_check_and_create_with_a_fake_client(monkeypatch, capsys):
    setup = load_setup()
    client = FakeRekognitionClient()
    client.faces = {"a": "u", "b": "u"}
    client.script["detect_faces"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    client.script["index_faces"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    client.script["search_faces_by_image"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    monkeypatch.setattr(setup, "_client", lambda region: client)
    monkeypatch.setattr(setup, "_account_id", lambda region: "123456789012")
    assert setup.main(["--region", "ap-south-1", "--collection", "clockyn-faces", "check"]) == 0
    out = capsys.readouterr().out
    assert "region:        ap-south-1" in out and "collection id: clockyn-faces" in out and "face count:    2" in out
    assert "READY" in out and "rekognition:IndexFaces" in out

    # one action denied: named in the report, exit code 1
    client2 = FakeRekognitionClient()
    client2.script["index_faces"] = [client_error("AccessDeniedException", "no")]
    client2.script["detect_faces"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    monkeypatch.setattr(setup, "_client", lambda region: client2)
    assert setup.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "DENIED" in out and "missing IAM action rekognition:IndexFaces" in out and "NOT READY" in out

    # missing collection: check fails and says how to create it; create makes it
    client3 = FakeRekognitionClient()
    client3.collection_exists = False
    monkeypatch.setattr(setup, "_client", lambda region: client3)
    assert setup.main(["check"]) == 1
    assert "does NOT exist" in capsys.readouterr().out
    assert setup.main(["create"]) == 0 and client3.collection_exists is True and "created collection" in capsys.readouterr().out
    assert setup.main(["create"]) == 0 and "already exists" in capsys.readouterr().out
    client3.collection_exists = False
    client3.script["create_collection"] = [client_error("AccessDeniedException", "no")]
    assert setup.main(["create"]) == 1 and "CreateCollection" in capsys.readouterr().err
