"""Thin glue between the punch / register flows and the face service (AWS Rekognition or the development mock).

`prepare_face` (local decode, EXIF, quality, hard pre-check) and `verify_face` (service call, optional local anti-spoof on a
crop of the Rekognition bounding box) are what core_functions/face_attendance_functions.py runs on the face thread pool.
"""
from ml import face_service
from ml.face import anti_spoof_check, crop_face, prepare_face, spoof_check_enabled

__all__ = ["prepare_face", "verify_face"]


def verify_face(prepared: dict, user_id: str) -> dict:
    """Verify a prepared photo (the success dict of `prepare_face`) against the faces enrolled for `user_id`.

    Result: {"match": bool, "score": 0..1 | None, "code", "reason", "message", "face_found"}. `score` is the Rekognition
    similarity / 100 (the 0..1 scale of the old cosine score; stored in face_attempt_log.score). The match threshold is
    env REKOGNITION_THRESHOLD (similarity 0..100, default 90): a face needs `similarity >= threshold`."""
    spoof_check = None
    if spoof_check_enabled():
        image = prepared.get("image")

        def spoof_check(box):
            return anti_spoof_check(crop_face(image, box))

    result = face_service.verify(user_id, prepared["jpeg"], spoof_check=spoof_check)
    return {
        "match": result.match,
        "score": result.score,
        "code": result.code,
        "reason": result.reason,
        "message": result.message,
        "face_found": result.face_found,
    }
