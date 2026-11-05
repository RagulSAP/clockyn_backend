"""Image quality measurements used for diagnostics only.

Nothing here may ever reject an image that face detection accepted: callers use
`classify_failure_reason` only AFTER a failed detection, and `measure_quality`
only to log numbers. Heavy libraries are optional (OpenCV is used for the
Laplacian when present, otherwise NumPy).
"""
import os

import numpy as np


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# Tunable (approximate) thresholds for the failure-reason classifier.
DARK_LUMINANCE = _env_float("FACE_DARK_THRESHOLD", 45.0)   # mean luminance, 0-255
BLUR_VARIANCE = _env_float("FACE_BLUR_THRESHOLD", 40.0)    # variance of Laplacian
MIN_SIDE_PX = _env_float("FACE_MIN_SIDE", 200.0)           # shorter image side, px


def _to_gray(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[..., :3].astype(np.float32)
        return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return arr.astype(np.float32)


def _laplacian_variance(gray: np.ndarray) -> float:
    try:
        import cv2  # optional
        lap = getattr(cv2, "Laplacian", None)
        if lap is not None:
            return float(lap(gray.astype(np.uint8), getattr(cv2, "CV_64F", -1)).var())
    except Exception:
        pass
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    c = gray[1:-1, 1:-1]
    lap = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]) - 4.0 * c
    return float(lap.var())


def measure_quality(image) -> dict:
    """Return {w, h, brightness, blur} for a decoded RGB array (never raises)."""
    try:
        h, w = image.shape[:2]
        gray = _to_gray(image)
        return {
            "w": int(w),
            "h": int(h),
            "brightness": round(float(gray.mean()), 1),
            "blur": round(_laplacian_variance(gray), 1),
        }
    except Exception:
        return {}


def classify_failure_reason(quality: dict) -> str:
    """Map measurements to too_small | too_dark | too_blurry | unknown."""
    if not quality:
        return "unknown"
    w, h = quality.get("w"), quality.get("h")
    if w is not None and h is not None and min(w, h) < MIN_SIDE_PX:
        return "too_small"
    if quality.get("brightness") is not None and quality["brightness"] < DARK_LUMINANCE:
        return "too_dark"
    if quality.get("blur") is not None and quality["blur"] < BLUR_VARIANCE:
        return "too_blurry"
    return "unknown"
