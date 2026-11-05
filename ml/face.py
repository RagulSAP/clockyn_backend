"""Local image handling for face punching. Matching is NOT done here any more: it lives in ml/face_service.py
(AWS Rekognition collection or the development mock).

What stays local (no AWS call, so it also saves API cost):
* `load_image`     decode, apply the EXIF rotation, RGB, downscale to FACE_MAX_WIDTH (the array the quality numbers use).
* `prepare_face`   decode + quality measurement + a cheap hard pre-check + the JPEG that is sent to the service.
* `anti_spoof_check` / `crop_face`  the local onnxruntime liveness model on a face crop (the crop comes from the
  Rekognition bounding box). Off by default (it was disabled "for testing" in the old code); env FACE_ANTISPOOF=1.

The heavy libraries (cv2, onnxruntime) are imported lazily, so this module imports on a machine without them.
"""
import io
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from ml import face_service
from ml.face_quality import classify_failure_reason, measure_quality
from ml.rekognition_face import encode_pil_jpeg

logger = logging.getLogger("face.metrics")

SPOOF_MODEL_PATH = Path(__file__).resolve().parent / "models" / "modelrgb.onnx"
SPOOF_INPUT_SIZE = 112
UNREADABLE = "Face not captured properly"
CROP_MARGIN = 0.1                  # fraction of the box size added on every side of the Rekognition box

# Hard pre-check: photos that cannot possibly contain a usable face are refused locally (no paid API call). These are far
# below the diagnostic thresholds of ml/face_quality.py (which never reject anything): only degenerate photos hit them.
HARD_MIN_SIDE_PX = 80
HARD_DARK_LUMINANCE = 10.0
HARD_FLAT_VARIANCE = 1.0


def _env_number(name, default, lo, hi, cast=int):
    """Read a numeric env var; invalid or out-of-range values fall back to the default."""
    try:
        value = cast(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    if value < lo or value > hi:
        return default
    return value


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# FACE_MAX_WIDTH: the decoded array used for the quality numbers is downscaled to this width (the thresholds of
# ml/face_quality.py were tuned at 640). The photo sent to Rekognition is separate (longest side 1920 px).
FACE_MAX_WIDTH = _env_number("FACE_MAX_WIDTH", 640, 160, 2048)


# =====================================================
# IMAGE LOAD
# =====================================================
def _open_oriented(img_bytes) -> Optional[Image.Image]:
    """Pillow image with the EXIF rotation applied (phone cameras store rotation in EXIF; a portrait photo would otherwise
    reach the detector sideways and no face would be found). None when the bytes are not an image."""
    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
    except Exception:
        return None
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:
        pass
    return img


def _to_rgb_array(img: Image.Image, max_width: int) -> np.ndarray:
    rgb = img.convert("RGB")
    w, h = rgb.size
    if w > max_width:
        rgb = rgb.resize((max_width, max(1, int(h * max_width / w))), Image.Resampling.BILINEAR)
    return np.array(rgb)


def load_image(img_bytes):
    """RGB array (EXIF rotation applied, at most FACE_MAX_WIDTH wide) or None for an unreadable image."""
    img = _open_oriented(img_bytes)
    if img is None:
        return None
    try:
        return _to_rgb_array(img, FACE_MAX_WIDTH)
    except Exception:
        return None


def hard_reject_reason(quality: dict) -> Optional[str]:
    """too_small | too_dark | too_blurry when the photo is degenerate (tiny, black, or one flat colour), else None.
    Env FACE_PRECHECK: unset = on for Rekognition and off for the mock backend (synthetic test images); 1 / 0 force it."""
    if not quality or not _truthy("FACE_PRECHECK", face_service.backend_name() != "mock"):
        return None
    w, h = quality.get("w"), quality.get("h")
    if w is not None and h is not None and min(w, h) < HARD_MIN_SIDE_PX:
        return "too_small"
    if quality.get("brightness") is not None and quality["brightness"] < HARD_DARK_LUMINANCE:
        return "too_dark"
    if quality.get("blur") is not None and quality["blur"] < HARD_FLAT_VARIANCE:
        return "too_blurry"
    return None


# =====================================================
# ANTI SPOOF
# =====================================================
_spoof = None


def _spoof_session():
    global _spoof
    if _spoof is None:
        import onnxruntime as ort
        ort.set_default_logger_severity(3)
        session = ort.InferenceSession(str(SPOOF_MODEL_PATH), providers=["CPUExecutionProvider"])
        _spoof = (session, session.get_inputs()[0].name)
    return _spoof


def spoof_check_enabled() -> bool:
    return _truthy("FACE_ANTISPOOF", False)


def crop_face(image: np.ndarray, box: Tuple[float, float, float, float], margin: float = CROP_MARGIN) -> np.ndarray:
    """Pixel crop of a Rekognition BoundingBox: `box` = (left, top, width, height) as fractions of the image; `margin` is
    added on every side (fraction of the box size) and the result is clamped to the image. May be empty (degenerate box)."""
    h, w = image.shape[:2]
    left, top, bw, bh = box
    x1 = int(round((left - margin * bw) * w))
    y1 = int(round((top - margin * bh) * h))
    x2 = int(round((left + bw + margin * bw) * w))
    y2 = int(round((top + bh + margin * bh) * h))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return image[0:0, 0:0]
    return image[y1:y2, x1:x2]


def anti_spoof_check(face_crop):
    """True when the crop looks like a real face (model score < 0.5: lower score = REAL). False on an empty crop or error."""
    if face_crop is None or face_crop.size == 0:
        return False
    try:
        import cv2
        session, input_name = _spoof_session()
        img = cv2.resize(face_crop, (SPOOF_INPUT_SIZE, SPOOF_INPUT_SIZE))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = np.expand_dims(img, axis=0)
        img = np.ascontiguousarray(img)

        output = session.run(None, {input_name: img})[0]
        score = float(output[0][0])

        # IMPORTANT:
        # lower score = REAL face
        return score < 0.5

    except Exception as e:
        logger.warning("anti-spoof check failed: %s", e)
        return False


# =====================================================
# PREPARE (decode + quality + the photo that goes to the service)
# =====================================================
def prepare_face(img_bytes, for_enroll=False):
    """Decode and measure one photo. Result keys:

    success=True : `jpeg` (EXIF-rotated JPEG <= 5 MB, longest side <= 1920 px: what is sent to the service), `image` (the RGB
                   array the quality numbers and the anti-spoof crop use) and `quality` ({w, h, brightness, blur}).
    success=False: `message`, `code` (`image_unreadable` | `no_face`), `reason` (too_small | too_dark | too_blurry | unknown)
                   and `quality`. The reason is only computed after the photo failed; a photo that passes the hard
                   pre-check is never rejected locally (the service decides).
    """
    pil = _open_oriented(img_bytes)
    if pil is None:
        return {"success": False, "message": UNREADABLE, "code": "image_unreadable", "reason": "unknown", "quality": {}}
    try:
        image = _to_rgb_array(pil, FACE_MAX_WIDTH)
    except Exception:
        return {"success": False, "message": UNREADABLE, "code": "image_unreadable", "reason": "unknown", "quality": {}}

    quality = measure_quality(image)
    hard = hard_reject_reason(quality)
    if hard:
        return {"success": False, "message": "No face detected", "code": "no_face", "reason": hard, "quality": quality}

    try:
        jpeg = encode_pil_jpeg(pil)
    except Exception:
        return {"success": False, "message": UNREADABLE, "code": "image_unreadable", "reason": "unknown", "quality": quality}
    return {"success": True, "jpeg": jpeg, "image": image, "quality": quality}



def reason_for(quality: dict, reported: Optional[str] = None) -> str:
    """Failure reason of a refused photo: the one the service reported (several faces, quality filter) or, when it gave
    none, the one derived from the pixels (too_small | too_dark | too_blurry | unknown)."""
    return reported or classify_failure_reason(quality)


# =====================================================
# WARM-UP
# =====================================================
def warmup_model():
    """Start-up hook. Rekognition needs no local model: this only checks the backend configuration. Never raises."""
    try:
        return face_service.warmup()
    except Exception as e:
        logger.warning("face warm-up failed: %s", e)
        return False
