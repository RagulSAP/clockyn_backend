"""AWS Rekognition (collections) adapter: the only module that talks to the Rekognition API.

Callers use `ml/face_service.py`, never this module directly. Operations (all on ONE collection, env
REKOGNITION_COLLECTION_ID, default `clockyn-faces`):

* `detect_faces`  DetectFaces: BoundingBox / Confidence / Quality / Pose of every face (count faces, crop for anti-spoof).
* `index_face`    IndexFaces (MaxFaces=1, QualityFilter=AUTO, ExternalImageId = encoded user id).
* `search`        SearchFacesByImage (FaceMatchThreshold = env REKOGNITION_THRESHOLD, similarity 0..100).
* `delete_faces`  DeleteFaces by FaceId (chunks of 4096, the API maximum).

Every Rekognition / transport failure is raised as `RekognitionError(kind, message, retryable)`; `kind` is one of
no_face, invalid_image, image_too_large, collection_missing, throttled, access_denied, unavailable, invalid_parameter,
already_exists, unknown. Retries (throttling, 5xx, timeouts) are done by botocore (retries mode `standard`, max_attempts from
REKOGNITION_MAX_RETRIES), so a RekognitionError(kind="throttled") means the retries are already used up.

Image bytes are NEVER logged: only operations, kinds, collection id and ids.
"""
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

try:  # the app loads .env in ml/s3.py as well; this keeps the module usable on its own (scripts, tests)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv missing
    pass

logger = logging.getLogger("face.rekognition")

DEFAULT_COLLECTION_ID = "clockyn-faces"
DEFAULT_REGION = "ap-south-1"
DEFAULT_THRESHOLD = 90.0
DEFAULT_MAX_RETRIES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024        # Rekognition limit for image bytes passed in the request
MAX_IMAGE_SIDE = 1920                    # longest side we send (px); larger photos are downscaled
JPEG_QUALITY = 90
DELETE_CHUNK = 4096                      # DeleteFaces accepts at most 4096 FaceIds per call
SEARCH_MAX_FACES = 5
MULTI_FACE_MIN_CONFIDENCE = 90.0
MULTI_FACE_MIN_RATIO = 0.5               # another face counts as "a second person" from half the main face's width

# DetectFaces / IndexFaces / SearchFacesByImage operation name -> boto3 client method
_METHODS = {
    "DetectFaces": "detect_faces",
    "IndexFaces": "index_faces",
    "SearchFacesByImage": "search_faces_by_image",
    "DeleteFaces": "delete_faces",
    "CreateCollection": "create_collection",
    "DescribeCollection": "describe_collection",
    "ListFaces": "list_faces",
    "ListCollections": "list_collections",
}
# IndexFaces `UnindexedFaces[].Reasons` -> the failure `reason` vocabulary of the app (too_dark | too_blurry | too_small)
_UNINDEXED_REASONS = (("SMALL_BOUNDING_BOX", "too_small"), ("LOW_BRIGHTNESS", "too_dark"), ("LOW_SHARPNESS", "too_blurry"))


# ------------------------------------------------------------------------------------------------ configuration
def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def region() -> str:
    return (os.getenv("REKOGNITION_REGION") or os.getenv("AWS_S3_REGION") or DEFAULT_REGION).strip()


def collection_id() -> str:
    return (os.getenv("REKOGNITION_COLLECTION_ID") or DEFAULT_COLLECTION_ID).strip()


def auto_create_enabled() -> bool:
    return _truthy("REKOGNITION_AUTO_CREATE")


def match_threshold() -> float:
    """REKOGNITION_THRESHOLD: similarity 0..100 needed for a match (default 90.0). Invalid values fall back."""
    raw = os.getenv("REKOGNITION_THRESHOLD")
    if raw is None or not str(raw).strip():
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("REKOGNITION_THRESHOLD=%r is not a number: using %s", raw, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    if value != value or value < 0.0 or value > 100.0:
        logger.warning("REKOGNITION_THRESHOLD=%r is outside 0..100: using %s", raw, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    return value


def max_retries() -> int:
    try:
        value = int(os.getenv("REKOGNITION_MAX_RETRIES", DEFAULT_MAX_RETRIES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_RETRIES
    return value if 1 <= value <= 10 else DEFAULT_MAX_RETRIES


def build_client():
    """boto3 `rekognition` client: region per `region()`, botocore retries mode `standard`, timeouts 5 s connect / 10 s read.
    Credentials: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY when both are set, otherwise boto3's default chain (IAM role)."""
    import boto3
    from botocore.config import Config
    config = Config(
        retries={"mode": "standard", "max_attempts": max_retries()},
        connect_timeout=5,
        read_timeout=10,
    )
    kwargs = {"region_name": region(), "config": config}
    key, secret = os.getenv("AWS_ACCESS_KEY_ID") or None, os.getenv("AWS_SECRET_ACCESS_KEY") or None
    if key and secret:
        kwargs.update(aws_access_key_id=key, aws_secret_access_key=secret)
    return boto3.client("rekognition", **kwargs)


# ------------------------------------------------------------------------------------------------ user id <-> ExternalImageId
_SAFE_CHARS = re.compile(r"[A-Za-z0-9_.\-]")
EXTERNAL_ID_MAX = 255


def encode_external_id(user_id: str) -> str:
    """User id -> Rekognition ExternalImageId (allowed characters `[A-Za-z0-9_.:-]`, 1..255).

    Mapping (documented, unique and reversible): `A-Z a-z 0-9 _ . -` stay as they are; every other character, including
    `:` itself, becomes `:` + two hex digits per UTF-8 byte (`a b` -> `a:20b`, `:` -> `:3a`). A UUID is unchanged.
    `decode_external_id` is the exact inverse."""
    text = str(user_id)
    if not text:
        raise ValueError("user id must not be empty")
    out = []
    for ch in text:
        if _SAFE_CHARS.fullmatch(ch):
            out.append(ch)
        else:
            out.extend(":%02x" % b for b in ch.encode("utf-8"))
    encoded = "".join(out)
    if len(encoded) > EXTERNAL_ID_MAX:
        raise ValueError("user id is too long for a Rekognition ExternalImageId")
    return encoded


def decode_external_id(external_id: str) -> str:
    raw = bytearray()
    i = 0
    text = str(external_id)
    while i < len(text):
        if text[i] == ":":
            raw.append(int(text[i + 1:i + 3], 16))
            i += 3
        else:
            raw += text[i].encode("ascii")
            i += 1
    return raw.decode("utf-8")


def safe_decode_external_id(external_id) -> Optional[str]:
    """Like decode_external_id but None for a value this app did not write."""
    if not external_id:
        return None
    try:
        return decode_external_id(external_id)
    except (ValueError, UnicodeError):
        return None


# ------------------------------------------------------------------------------------------------ errors
class RekognitionError(Exception):
    """A Rekognition / transport failure with a machine-readable `kind` (see the module docstring)."""

    def __init__(self, kind: str, message: str, retryable: bool = False, operation: Optional[str] = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.operation = operation


_ACCESS_CODES = {"AccessDeniedException", "AccessDenied", "UnauthorizedOperation"}
_CREDENTIAL_CODES = {"UnrecognizedClientException", "InvalidSignatureException", "ExpiredTokenException",
                     "ExpiredToken", "InvalidClientTokenId", "SignatureDoesNotMatch", "InvalidAccessKeyId"}
_THROTTLE_CODES = {"ProvisionedThroughputExceededException", "ThrottlingException", "Throttling", "LimitExceededException",
                   "RequestLimitExceeded", "TooManyRequestsException"}
_UNAVAILABLE_CODES = {"InternalServerError", "ServiceUnavailableException", "ServiceUnavailable", "InternalFailure"}
_TRANSPORT_NAMES = {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError",
                    "ProxyConnectionError", "SSLError", "HTTPClientError", "ConnectionError", "TimeoutError"}
_NO_CREDENTIAL_NAMES = {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError", "TokenRetrievalError"}


def _error_code_and_message(exc: Exception) -> Tuple[str, str]:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error") or {}
        return str(err.get("Code") or ""), str(err.get("Message") or "")
    return "", ""


def map_exception(exc: Exception, operation: str, collection: Optional[str] = None, reg: Optional[str] = None) -> RekognitionError:
    """Translate a boto3 / botocore exception into a RekognitionError. Never includes request payloads."""
    if isinstance(exc, RekognitionError):
        return exc
    collection = collection or collection_id()
    reg = reg or region()
    code, message = _error_code_and_message(exc)
    name = type(exc).__name__
    action = "rekognition:" + operation
    scope = "arn:aws:rekognition:%s:<ACCOUNT_ID>:collection/%s" % (reg, collection)
    if operation == "DetectFaces":
        scope = "*"

    if code == "InvalidParameterException":
        if "face" in message.lower():          # "There are no faces in the image. Should be at least 1."
            return RekognitionError("no_face", "Rekognition found no usable face in the image", operation=operation)
        return RekognitionError("invalid_parameter", "Rekognition rejected a parameter of %s (%s)" % (operation, message[:200]),
                                operation=operation)
    if code == "ResourceAlreadyExistsException":
        return RekognitionError("already_exists", "Rekognition collection '%s' already exists" % collection, operation=operation)
    if code == "InvalidImageFormatException":
        return RekognitionError("invalid_image", "Rekognition cannot read the image (JPEG or PNG required)", operation=operation)
    if code == "ImageTooLargeException":
        return RekognitionError("image_too_large", "The image is larger than the Rekognition limit", operation=operation)
    if code == "ResourceNotFoundException":
        return RekognitionError(
            "collection_missing",
            "Rekognition collection '%s' does not exist in %s. Create it: python scripts/rekognition_setup.py create "
            "(or set REKOGNITION_AUTO_CREATE=1 with a role that may CreateCollection)." % (collection, reg),
            operation=operation)
    if code in _THROTTLE_CODES:
        return RekognitionError("throttled", "Rekognition is throttling requests (%s); retries exhausted" % operation,
                                retryable=True, operation=operation)
    if code in _ACCESS_CODES:
        return RekognitionError(
            "access_denied",
            "AWS denied %s on %s. Grant the app's IAM role this action (least-privilege policy: "
            "python scripts/rekognition_setup.py policy)." % (action, scope),
            operation=operation)
    if code in _CREDENTIAL_CODES or name in _NO_CREDENTIAL_NAMES:
        return RekognitionError(
            "access_denied",
            "AWS credentials are missing, invalid or expired for %s (set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY or attach an "
            "IAM role to the service)." % action,
            operation=operation)
    if code in _UNAVAILABLE_CODES or name in _TRANSPORT_NAMES:
        return RekognitionError("unavailable", "Rekognition is not reachable or failed (%s: %s)" % (operation, code or name),
                                retryable=True, operation=operation)
    return RekognitionError("unknown", "Unexpected Rekognition error in %s (%s)" % (operation, code or name), operation=operation)


# ------------------------------------------------------------------------------------------------ images
def _resample():
    from PIL import Image
    return Image.Resampling.LANCZOS


def _encode_jpeg_within(img, max_bytes: int, max_side: int) -> bytes:
    """Re-encode a Pillow image as JPEG with longest side <= max_side and size <= max_bytes (quality, then scale, steps)."""
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), _resample())
    quality = JPEG_QUALITY
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
        if quality > 50:
            quality -= 10
            continue
        w, h = img.size
        if min(w, h) < 64:
            raise RekognitionError("image_too_large", "The image cannot be reduced below the Rekognition size limit")
        img = img.resize((int(w * 0.8), int(h * 0.8)), _resample())


def encode_pil_jpeg(img, max_bytes: int = MAX_IMAGE_BYTES, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    return _encode_jpeg_within(img, max_bytes, max_side)


def fit_image_bytes(data: bytes, max_bytes: int = MAX_IMAGE_BYTES, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    """Bytes Rekognition accepts: JPEG/PNG, <= max_bytes, longest side <= max_side, EXIF rotation applied.
    A photo that already satisfies all of that is returned unchanged; otherwise it is re-encoded as JPEG with Pillow.
    Raises RekognitionError(kind="invalid_image") when the bytes are not a decodable image."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise RekognitionError("invalid_image", "The photo is not a readable image")
    orientation = 1
    try:
        orientation = img.getexif().get(0x0112, 1) or 1
    except Exception:
        pass
    if ((img.format or "").upper() in ("JPEG", "PNG") and img.mode in ("RGB", "L") and orientation == 1
            and len(data) <= max_bytes and max(img.size) <= max_side):
        return data
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:
        pass
    return _encode_jpeg_within(img, max_bytes, max_side)


# ------------------------------------------------------------------------------------------------ results
@dataclass
class Face:
    box: Tuple[float, float, float, float]           # (left, top, width, height), each 0..1 of the image
    confidence: float = 0.0
    brightness: Optional[float] = None
    sharpness: Optional[float] = None
    pose: dict = field(default_factory=dict)

    @property
    def area(self) -> float:
        return max(0.0, self.box[2]) * max(0.0, self.box[3])


@dataclass
class IndexOutcome:
    face_id: Optional[str]
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)   # raw Rekognition `UnindexedFaces[].Reasons` when not indexed


@dataclass
class Match:
    user_id: Optional[str]      # decoded ExternalImageId; None for a face this app did not write
    similarity: float           # 0..100
    face_id: str = ""


def _parse_face(detail: dict) -> Face:
    box = detail.get("BoundingBox") or {}
    quality = detail.get("Quality") or {}
    return Face(
        box=(float(box.get("Left", 0.0)), float(box.get("Top", 0.0)), float(box.get("Width", 0.0)), float(box.get("Height", 0.0))),
        confidence=float(detail.get("Confidence", 0.0)),
        brightness=quality.get("Brightness"),
        sharpness=quality.get("Sharpness"),
        pose=dict(detail.get("Pose") or {}),
    )


def pick_primary(faces: Sequence[Face], min_ratio: float = MULTI_FACE_MIN_RATIO,
                 min_confidence: float = MULTI_FACE_MIN_CONFIDENCE) -> Tuple[Optional[Face], int]:
    """(largest face, number of OTHER significant faces). A face is significant when its confidence is >= min_confidence and
    its width is >= min_ratio of the largest face's width, so a passer-by far in the background does not block a punch."""
    if not faces:
        return None, 0
    ordered = sorted(faces, key=lambda f: f.area, reverse=True)
    primary = ordered[0]
    others = sum(1 for f in ordered[1:]
                 if f.confidence >= min_confidence and primary.box[2] > 0 and f.box[2] >= min_ratio * primary.box[2])
    return primary, others


def reason_from_unindexed(reasons: Sequence[str]) -> Optional[str]:
    """IndexFaces `Reasons` -> too_small | too_dark | too_blurry, or None (the caller classifies from the pixels)."""
    wanted = set(reasons or [])
    for raw, mapped in _UNINDEXED_REASONS:
        if raw in wanted:
            return mapped
    return None


# ------------------------------------------------------------------------------------------------ client wrapper
class RekognitionFace:
    def __init__(self, client=None, collection: Optional[str] = None, threshold: Optional[float] = None):
        self._client = client
        self._collection = collection
        self._threshold = threshold

    # -- configuration
    @property
    def client(self):
        if self._client is None:
            self._client = build_client()
        return self._client

    @property
    def collection_id(self) -> str:
        return self._collection or collection_id()

    @property
    def threshold(self) -> float:
        return self._threshold if self._threshold is not None else match_threshold()

    # -- plumbing
    def _invoke(self, operation: str, kwargs: dict):
        try:
            return getattr(self.client, _METHODS[operation])(**kwargs)
        except RekognitionError:
            raise
        except Exception as exc:
            raise map_exception(exc, operation, self.collection_id, region()) from exc

    def _call(self, operation: str, **kwargs):
        try:
            response = self._invoke(operation, kwargs)
        except RekognitionError as err:
            if err.kind == "collection_missing" and operation != "CreateCollection" and auto_create_enabled():
                logger.warning("collection %s is missing: creating it (REKOGNITION_AUTO_CREATE=1)", self.collection_id)
                self.create_collection()
                response = self._invoke(operation, kwargs)
            else:
                logger.warning("rekognition %s failed: %s", operation, err.kind)
                raise
        return response

    # -- collection management
    def create_collection(self) -> bool:
        """Create the collection; False when it already exists."""
        try:
            self._invoke("CreateCollection", {"CollectionId": self.collection_id})
            return True
        except RekognitionError as err:
            if err.kind == "already_exists":
                return False
            raise

    def describe_collection(self) -> dict:
        return self._call("DescribeCollection", CollectionId=self.collection_id)

    # -- face operations
    def prepare(self, image: bytes) -> bytes:
        return fit_image_bytes(image)

    def detect_faces(self, image: bytes) -> List[Face]:
        """Every face of the (already fitted) image; [] when there is none."""
        try:
            response = self._call("DetectFaces", Image={"Bytes": image}, Attributes=["DEFAULT"])
        except RekognitionError as err:
            if err.kind == "no_face":
                return []
            raise
        return [_parse_face(d) for d in (response.get("FaceDetails") or [])]

    def index_face(self, image: bytes, user_id: str) -> IndexOutcome:
        response = self._call(
            "IndexFaces",
            CollectionId=self.collection_id,
            Image={"Bytes": image},
            ExternalImageId=encode_external_id(user_id),
            MaxFaces=1,
            QualityFilter="AUTO",
            DetectionAttributes=[],
        )
        records = response.get("FaceRecords") or []
        if records:
            face = records[0].get("Face") or {}
            if face.get("FaceId"):
                return IndexOutcome(face_id=str(face["FaceId"]), confidence=float(face.get("Confidence", 0.0)))
        reasons: List[str] = []
        for unindexed in response.get("UnindexedFaces") or []:
            reasons.extend(unindexed.get("Reasons") or [])
        return IndexOutcome(face_id=None, reasons=reasons)

    def search(self, image: bytes) -> List[Match]:
        """Matches (best first) of the largest face of the image against the collection, at or above the threshold."""
        response = self._call(
            "SearchFacesByImage",
            CollectionId=self.collection_id,
            Image={"Bytes": image},
            MaxFaces=SEARCH_MAX_FACES,
            FaceMatchThreshold=float(self.threshold),
            QualityFilter="AUTO",
        )
        matches = []
        for entry in response.get("FaceMatches") or []:
            face = entry.get("Face") or {}
            matches.append(Match(
                user_id=safe_decode_external_id(face.get("ExternalImageId")),
                similarity=float(entry.get("Similarity", 0.0)),
                face_id=str(face.get("FaceId") or ""),
            ))
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches

    def delete_faces(self, face_ids: Sequence[str]) -> int:
        """Delete faces from the collection; returns how many Rekognition reports as deleted. A missing collection means
        there is nothing left to delete (logged, not an error)."""
        ids = [f for f in face_ids if f]
        deleted = 0
        for start in range(0, len(ids), DELETE_CHUNK):
            chunk = ids[start:start + DELETE_CHUNK]
            try:
                response = self._call("DeleteFaces", CollectionId=self.collection_id, FaceIds=chunk)
            except RekognitionError as err:
                if err.kind == "collection_missing":
                    logger.warning("DeleteFaces: collection %s does not exist, nothing to delete", self.collection_id)
                    return deleted
                raise
            deleted += len(response.get("DeletedFaces") or [])
        return deleted
