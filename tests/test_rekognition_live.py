"""OPTIONAL live test against real AWS Rekognition. Skipped unless BOTH are set:

    REKOGNITION_LIVE=1
    REKOGNITION_TEST_IMAGE=<path to a real JPEG/PNG photo of ONE face>

It creates a throw-away collection `clockyn-test-<hex>` (never the production collection), enrols the photo under a
throw-away user id, verifies that the same photo matches (and that another user id does not), and deletes the faces and the
collection in `finally`. It needs the operator permissions of `scripts/rekognition_setup.py policy --admin` (CreateCollection /
DeleteCollection plus the face actions on collection/clockyn-test-*) and uses the AWS settings of the environment
(REKOGNITION_REGION / AWS_S3_REGION, credentials). It makes a handful of billable Rekognition calls.
No database is used: the user_face_index store is replaced by an in-memory one.
Run: REKOGNITION_LIVE=1 REKOGNITION_TEST_IMAGE=/path/face.jpg python -m pytest tests/test_rekognition_live.py -q -s
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIVE = os.getenv("REKOGNITION_LIVE") == "1"
IMAGE = os.getenv("REKOGNITION_TEST_IMAGE") or ""

pytestmark = pytest.mark.skipif(
    not (LIVE and IMAGE and Path(IMAGE).is_file()),
    reason="live Rekognition test: set REKOGNITION_LIVE=1 and REKOGNITION_TEST_IMAGE=<face photo path>",
)


def test_live_enrol_verify_and_cleanup(monkeypatch):
    from face_fakes import FakeStore
    import ml.face_service as fs
    import ml.rekognition_face as rf

    monkeypatch.setenv("FACE_BACKEND", "rekognition")
    collection = "clockyn-test-" + uuid.uuid4().hex[:12]
    user, stranger = "live-" + uuid.uuid4().hex[:12], "live-" + uuid.uuid4().hex[:12]
    photo = Path(IMAGE).read_bytes()

    rek = rf.RekognitionFace(client=rf.build_client(), collection=collection, threshold=rf.match_threshold())
    store = FakeStore()
    be = fs.RekognitionBackend(rek, store)
    created = False
    try:
        assert rek.create_collection() is True
        created = True
        result = be.register(user, [photo] * 4)
        assert result.success, (result.code, result.reason, result.message)
        assert len(result.face_ids) == 4 and be.is_registered(user)

        verdict = be.verify(user, photo)
        assert verdict.match is True and verdict.similarity is not None and verdict.similarity >= rek.threshold, verdict
        assert be.verify(stranger, photo).match is False                       # same face, but not enrolled for that id
        found = be.identify(photo)
        assert found is not None and found[0] == user

        removed = be.remove(user)
        assert removed.ok and removed.removed == 4 and not be.is_registered(user)
        assert be.verify(user, photo).match is False
    finally:
        try:
            be.remove(user)
        except Exception:
            pass
        if created:
            try:
                rek.client.delete_collection(CollectionId=collection)
            except Exception as exc:  # report, do not mask the test result
                print("WARNING: could not delete the test collection %s: %s" % (collection, exc))
