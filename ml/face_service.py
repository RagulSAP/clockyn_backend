"""Face recognition facade: the only module the application calls to enrol, verify and remove faces.

    register(user_id, photos)  -> RegisterResult   (>= 1 photos; the caller enforces the minimum of 4)
    verify(user_id, photo)     -> VerifyResult     (1:1: is this the face enrolled for `user_id`?)
    identify(photo)            -> (user_id, similarity) | None   (1:N; no caller needs it today)
    remove(user_id)            -> RemoveResult     (best effort; also drops the user_face_index rows)
    is_registered(user_id)     -> bool
    warmup()                   -> bool             (no-op for Rekognition; never raises)

Backend per env FACE_BACKEND: `rekognition` (default, AWS Rekognition collection, see ml/rekognition_face.py) or `mock`
(deterministic stand-in for local development and browser tests; refuses to start when APP_ENV=production).

Failure vocabulary (`code`, same words the app already shows): ok | no_face (+ reason too_dark / too_blurry / too_small /
multiple_faces) | image_unreadable | no_match | spoof_detected | unavailable (Rekognition throttled / unreachable: answer
"try again") | server_error (configuration or unexpected: logged with the actionable cause, generic text to the client).
`reason` is only set here when Rekognition itself says why (IndexFaces quality filter, several faces); otherwise it is
None and the caller derives it from the pixels (ml/face_quality.py).

Similarity is Rekognition's 0..100 scale. `VerifyResult.score` = similarity / 100 (0..1), the scale the older cosine score
used and the scale stored in face_attempt_log.score (numeric(5,4)).

State: table user_face_index (migrations/035_user_face_index.sql): one row per indexed face (the Rekognition FaceId).
Logging never contains image bytes: only hashed user ids, similarities and Rekognition face ids.
"""
import hashlib
import io
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ml import rekognition_face as rf

logger = logging.getLogger("face.service")

NO_FACE_MESSAGE = "No face detected"
UNREADABLE_MESSAGE = "Face not captured properly"
MULTIPLE_FACES_MESSAGE = "More than one face detected. Please make sure only you are in the frame."
BUSY_MESSAGE = "The face service is busy. Please try again in a moment."
GENERIC_MESSAGE = "Something went wrong. Please try again."

# Mock backend: the (relative) face box it reports, so the anti-spoof crop path can be exercised without AWS.
MOCK_FACE_BOX = (0.2, 0.1, 0.6, 0.8)
MOCK_HASH_BYTES = 64 * 1024


# ------------------------------------------------------------------------------------------------ results
@dataclass
class RegisterResult:
    success: bool
    code: str = "ok"
    reason: Optional[str] = None
    message: str = ""
    failed_index: Optional[int] = None          # index of the photo that was refused (no_face / image_unreadable)
    face_ids: List[str] = field(default_factory=list)


@dataclass
class VerifyResult:
    match: bool
    similarity: Optional[float] = None          # 0..100; None when Rekognition returned no candidate at all
    face_found: bool = True
    code: str = "no_match"
    reason: Optional[str] = None
    message: str = ""
    box: Optional[Tuple[float, float, float, float]] = None   # relative (left, top, width, height) of the face

    @property
    def score(self) -> Optional[float]:
        return None if self.similarity is None else round(float(self.similarity) / 100.0, 4)


@dataclass
class RemoveResult:
    ok: bool = True
    removed: int = 0
    errors: List[str] = field(default_factory=list)


SpoofCheck = Callable[[Tuple[float, float, float, float]], bool]   # box -> True when the face is live


# ------------------------------------------------------------------------------------------------ helpers
def uid_hash(user_id) -> str:
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:10]


def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def is_production() -> bool:
    return (os.getenv("APP_ENV") or "").strip().lower() in ("production", "prod")


def warn_deprecated_settings() -> None:
    """The cosine threshold of the old local model means nothing for Rekognition: log that it is ignored."""
    if os.getenv("FACE_SIMILARITY_THRESHOLD") is not None:
        logger.warning("FACE_SIMILARITY_THRESHOLD is deprecated and ignored (Rekognition similarity is 0..100): "
                       "use REKOGNITION_THRESHOLD (currently %s)", rf.match_threshold())


def _failure_from_error(err: "rf.RekognitionError") -> Tuple[str, Optional[str], str]:
    """RekognitionError -> (code, reason, message); logs the actionable cause of configuration problems."""
    kind = err.kind
    if kind == "no_face":
        return "no_face", None, NO_FACE_MESSAGE
    if kind in ("invalid_image", "image_too_large"):
        return "image_unreadable", "unknown", UNREADABLE_MESSAGE
    if kind in ("throttled", "unavailable"):
        logger.warning("face service unavailable (%s): %s", kind, err)
        return "unavailable", None, BUSY_MESSAGE
    logger.error("face service misconfigured or failed (%s): %s", kind, err)
    return "server_error", None, GENERIC_MESSAGE


# ------------------------------------------------------------------------------------------------ storage (user_face_index)
class DbFaceStore:
    """user_face_index rows through the application's connection pool. Every method is one short transaction."""

    @contextmanager
    def _cursor(self, commit: bool = False):
        from core_functions import db_function as dbf
        from psycopg2.extras import RealDictCursor
        conn = dbf.get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
            dbf.return_connection(conn)

    def list_face_ids(self, user_id: str) -> List[str]:
        with self._cursor() as cur:
            cur.execute("SELECT face_id FROM user_face_index WHERE user_id = %s ORDER BY id", (user_id,))
            return [row["face_id"] for row in cur.fetchall()]

    def has_faces(self, user_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM user_face_index WHERE user_id = %s) AS registered", (user_id,))
            row = cur.fetchone()
            return bool(row and row["registered"])

    def add_faces(self, user_id: str, items: Sequence[Tuple[str, int]]) -> None:
        """Insert (face_id, photo_no) rows in ONE transaction (all or nothing)."""
        with self._cursor(commit=True) as cur:
            for face_id, photo_no in items:
                cur.execute(
                    "INSERT INTO user_face_index (user_id, face_id, photo_no) VALUES (%s, %s, %s)",
                    (user_id, face_id, photo_no),
                )

    def delete_face_ids(self, face_ids: Sequence[str]) -> int:
        ids = [f for f in face_ids if f]
        if not ids:
            return 0
        with self._cursor(commit=True) as cur:
            cur.execute("DELETE FROM user_face_index WHERE face_id = ANY(%s)", (ids,))
            return cur.rowcount


# ------------------------------------------------------------------------------------------------ Rekognition backend
class RekognitionBackend:
    name = "rekognition"

    def __init__(self, rek: Optional["rf.RekognitionFace"] = None, store=None):
        self._rek = rek
        self._store = store

    @property
    def rek(self) -> "rf.RekognitionFace":
        if self._rek is None:
            self._rek = rf.RekognitionFace()
        return self._rek

    @property
    def store(self):
        if self._store is None:
            self._store = DbFaceStore()
        return self._store

    # ---- registration
    def register(self, user_id: str, photos: Sequence[bytes]) -> RegisterResult:
        """Index every photo for the user, then store the rows, then delete the user's previous faces.

        All photos must be usable (the rule of the old design: one bad photo refuses the registration). The previous
        enrolment is replaced only AFTER the new faces are indexed and their rows committed, so a failed attempt never
        destroys a working registration. If the row insert fails the faces indexed by this call are deleted again."""
        if not photos:
            return RegisterResult(False, "no_face", None, NO_FACE_MESSAGE, 0)
        rek, store = self.rek, self.store
        new_ids: List[str] = []

        def fail(code, reason, message, index=None) -> RegisterResult:
            self._compensate(new_ids, user_id)
            return RegisterResult(False, code, reason, message, index)

        try:
            old_ids = store.list_face_ids(user_id)
            for index, raw in enumerate(photos):
                image = rek.prepare(raw)
                primary, others = rf.pick_primary(rek.detect_faces(image))
                if primary is None:
                    return fail("no_face", None, NO_FACE_MESSAGE, index)
                if others:
                    return fail("no_face", "multiple_faces", MULTIPLE_FACES_MESSAGE, index)
                outcome = rek.index_face(image, user_id)
                if not outcome.face_id:
                    return fail("no_face", rf.reason_from_unindexed(outcome.reasons), NO_FACE_MESSAGE, index)
                new_ids.append(outcome.face_id)
            store.add_faces(user_id, [(fid, n + 1) for n, fid in enumerate(new_ids)])
        except rf.RekognitionError as err:
            code, reason, message = _failure_from_error(err)
            return fail(code, reason, message, None if code in ("unavailable", "server_error") else len(new_ids))
        except Exception:
            logger.exception("face register failed for user %s", uid_hash(user_id))
            return fail("server_error", None, GENERIC_MESSAGE)

        self._delete_faces_and_rows(old_ids, user_id)
        logger.info("face register user=%s faces=%d replaced=%d", uid_hash(user_id), len(new_ids), len(old_ids))
        return RegisterResult(True, "ok", None, "", None, list(new_ids))

    def _compensate(self, face_ids: Sequence[str], user_id: str) -> None:
        """Delete faces indexed by a registration that did not complete (never raises; the ids are logged on failure so an
        operator can delete them by hand)."""
        if not face_ids:
            return
        try:
            self.rek.delete_faces(face_ids)
        except Exception as exc:
            logger.error("could not delete %d half-registered Rekognition faces of user %s (delete by FaceId): %s | %s",
                         len(face_ids), uid_hash(user_id), list(face_ids), exc)

    def _delete_faces_and_rows(self, face_ids: Sequence[str], user_id: str) -> RemoveResult:
        """Delete the faces from Rekognition, THEN their rows (rows survive a failed Rekognition call so it can be retried)."""
        result = RemoveResult()
        if not face_ids:
            return result
        try:
            result.removed = self.rek.delete_faces(face_ids)
            self.store.delete_face_ids(face_ids)
        except Exception as exc:
            result.ok = False
            result.errors.append("%s: %s" % (type(exc).__name__, exc))
            logger.warning("face cleanup for user %s incomplete (rows kept for a retry): %s", uid_hash(user_id), exc)
        return result

    # ---- verification
    def _single_face(self, image: bytes):
        """-> (face | None, failure VerifyResult | None) for an already fitted image."""
        primary, others = rf.pick_primary(self.rek.detect_faces(image))
        if primary is None:
            return None, VerifyResult(False, None, False, "no_face", None, NO_FACE_MESSAGE)
        if others:
            return None, VerifyResult(False, None, True, "no_face", "multiple_faces", MULTIPLE_FACES_MESSAGE, primary.box)
        return primary, None

    def verify(self, user_id: str, photo: bytes, spoof_check: Optional[SpoofCheck] = None) -> VerifyResult:
        try:
            image = self.rek.prepare(photo)
            face, failure = self._single_face(image)
            if failure is not None:
                return failure
            if spoof_check is not None and not spoof_check(face.box):
                return VerifyResult(False, None, True, "spoof_detected", None, "Spoof detected. Please use a real face.", face.box)
            matches = self.rek.search(image)
        except rf.RekognitionError as err:
            code, reason, message = _failure_from_error(err)
            return VerifyResult(False, None, code != "no_face", code, reason, message)
        except Exception:
            logger.exception("face verify failed for user %s", uid_hash(user_id))
            return VerifyResult(False, None, True, "server_error", None, GENERIC_MESSAGE)

        own = [m.similarity for m in matches if m.user_id == user_id]
        best_own = max(own) if own else None
        threshold = self.rek.threshold
        matched = best_own is not None and best_own >= threshold
        shown = best_own if best_own is not None else (max((m.similarity for m in matches), default=None))
        logger.info("face verify user=%s match=%s similarity=%s candidates=%d",
                    uid_hash(user_id), matched, None if shown is None else round(shown, 2), len(matches))
        if matched:
            return VerifyResult(True, best_own, True, "ok", None, "", face.box)
        return VerifyResult(False, shown, True, "no_match", None, "Face not recognized", face.box)

    def identify(self, photo: bytes) -> Optional[Tuple[str, float]]:
        """Best enrolled user for the face in the photo (>= threshold), else None. Raises RekognitionError on service errors."""
        try:
            image = self.rek.prepare(photo)
            face, failure = self._single_face(image)
            if failure is not None:
                return None
            matches = self.rek.search(image)
        except rf.RekognitionError as err:
            if err.kind in ("no_face", "invalid_image", "image_too_large"):
                return None
            raise
        threshold = self.rek.threshold
        for m in matches:                          # best first
            if m.user_id is not None and m.similarity >= threshold:
                return m.user_id, m.similarity
        return None

    # ---- state
    def remove(self, user_id: str) -> RemoveResult:
        try:
            ids = self.store.list_face_ids(user_id)
        except Exception as exc:
            logger.warning("face remove: could not list the faces of user %s: %s", uid_hash(user_id), exc)
            return RemoveResult(False, 0, ["%s: %s" % (type(exc).__name__, exc)])
        return self._delete_faces_and_rows(ids, user_id)

    def is_registered(self, user_id: str) -> bool:
        return self.store.has_faces(user_id)

    def warmup(self) -> bool:
        return True


# ------------------------------------------------------------------------------------------------ mock backend
class MockBackend:
    """Deterministic stand-in: any decodable image is "one face"; a photo matches the user when the SHA-256 of its first
    64 KB equals the one of a photo enrolled for that user, or always when FACE_MOCK_ALWAYS_MATCH=1. State lives in memory
    (per process). Refuses to be created when APP_ENV=production."""
    name = "mock"

    def __init__(self):
        if is_production():
            raise RuntimeError("FACE_BACKEND=mock is not allowed when APP_ENV=production")
        self._users: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        logger.warning("FACE_BACKEND=mock: faces are NOT really recognised (development / tests only)")

    @staticmethod
    def _digest(photo: bytes) -> str:
        return hashlib.sha256(bytes(photo)[:MOCK_HASH_BYTES]).hexdigest()

    @staticmethod
    def _decodable(photo: bytes) -> bool:
        try:
            from PIL import Image
            Image.open(io.BytesIO(photo)).load()
            return True
        except Exception:
            return False

    def register(self, user_id: str, photos: Sequence[bytes]) -> RegisterResult:
        if not photos:
            return RegisterResult(False, "no_face", None, NO_FACE_MESSAGE, 0)
        for index, photo in enumerate(photos):
            if not self._decodable(photo):
                return RegisterResult(False, "no_face", None, NO_FACE_MESSAGE, index)
        digests = [self._digest(p) for p in photos]
        with self._lock:
            self._users[user_id] = digests
        return RegisterResult(True, "ok", None, "", None, ["mock-" + d[:24] for d in digests])

    def verify(self, user_id: str, photo: bytes, spoof_check: Optional[SpoofCheck] = None) -> VerifyResult:
        if not self._decodable(photo):
            return VerifyResult(False, None, False, "no_face", None, NO_FACE_MESSAGE)
        if spoof_check is not None and not spoof_check(MOCK_FACE_BOX):
            return VerifyResult(False, None, True, "spoof_detected", None, "Spoof detected. Please use a real face.", MOCK_FACE_BOX)
        if self._digest(photo) in self._users.get(user_id, []):
            return VerifyResult(True, 100.0, True, "ok", None, "", MOCK_FACE_BOX)
        if _truthy("FACE_MOCK_ALWAYS_MATCH"):
            return VerifyResult(True, 95.0, True, "ok", None, "", MOCK_FACE_BOX)
        return VerifyResult(False, 10.0, True, "no_match", None, "Face not recognized", MOCK_FACE_BOX)

    def identify(self, photo: bytes) -> Optional[Tuple[str, float]]:
        if not self._decodable(photo):
            return None
        digest = self._digest(photo)
        with self._lock:
            for user_id, digests in self._users.items():
                if digest in digests:
                    return user_id, 100.0
            if _truthy("FACE_MOCK_ALWAYS_MATCH") and self._users:
                return next(iter(self._users)), 95.0
        return None

    def remove(self, user_id: str) -> RemoveResult:
        with self._lock:
            removed = len(self._users.pop(user_id, []))
        return RemoveResult(True, removed, [])

    def is_registered(self, user_id: str) -> bool:
        return bool(self._users.get(user_id))

    def warmup(self) -> bool:
        return True


# ------------------------------------------------------------------------------------------------ backend selection
_lock = threading.Lock()
_backend = None
_backend_name = None
_override = None


def backend_name() -> str:
    return (os.getenv("FACE_BACKEND") or "rekognition").strip().lower()


def _create_backend(name: str):
    warn_deprecated_settings()
    if name == "rekognition":
        return RekognitionBackend()
    if name == "mock":
        return MockBackend()
    raise ValueError("FACE_BACKEND must be 'rekognition' or 'mock' (got %r)" % name)


def get_backend():
    """The backend selected by FACE_BACKEND (created once per value; a test can pin one with `set_backend`)."""
    global _backend, _backend_name
    if _override is not None:
        return _override
    name = backend_name()
    with _lock:
        if _backend is None or _backend_name != name:
            _backend = _create_backend(name)
            _backend_name = name
        return _backend


def set_backend(backend) -> None:
    """Pin a backend object (tests); `set_backend(None)` goes back to the env-selected one."""
    global _override, _backend, _backend_name
    with _lock:
        _override = backend
        _backend = None
        _backend_name = None


# ------------------------------------------------------------------------------------------------ public API
def register(user_id: str, photos: Sequence[bytes]) -> RegisterResult:
    return get_backend().register(user_id, photos)


def verify(user_id: str, photo: bytes, spoof_check: Optional[SpoofCheck] = None) -> VerifyResult:
    return get_backend().verify(user_id, photo, spoof_check)


def identify(photo: bytes) -> Optional[Tuple[str, float]]:
    return get_backend().identify(photo)


def remove(user_id: str) -> RemoveResult:
    return get_backend().remove(user_id)


def is_registered(user_id: str) -> bool:
    return get_backend().is_registered(user_id)


def warmup() -> bool:
    """Cheap start-up hook. Never raises (a bad FACE_BACKEND is logged here and surfaces on the first face call)."""
    try:
        warn_deprecated_settings()
        return bool(get_backend().warmup())
    except Exception as exc:
        logger.warning("face service warm-up failed: %s", exc)
        return False
