"""DB-free, AWS-free tests for ml/rekognition_face.py (the Rekognition adapter) with a fake boto3 client.
Run: python -m pytest tests/test_rekognition_face.py -q
"""
import io
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ml.rekognition_face as rf  # noqa: E402
from face_fakes import FakeRekognitionClient, client_error, face_detail, jpeg_bytes, match_entry  # noqa: E402

ALLOWED_EXTERNAL = re.compile(r"^[A-Za-z0-9_.:\-]{1,255}$")


def make_rek(client=None, threshold=90.0, collection="clockyn-test"):
    client = client or FakeRekognitionClient()
    return rf.RekognitionFace(client=client, collection=collection, threshold=threshold), client


# --------------------------------------------------------------------------- user id <-> ExternalImageId
def test_external_id_round_trip_is_unique_and_within_the_allowed_charset():
    ids = [
        "3f2b8c1e-5d4a-4c8e-9b1f-0a1b2c3d4e5f", "plain_name-1.0", "a b", "a:b", "a%3ab", ":", "::", "a:3ab", "é", "日本", "x/y\\z",
        "user@example.com", "ünï", "tab\there", "a" * 200,
    ]
    encoded = [rf.encode_external_id(i) for i in ids]
    assert len(set(encoded)) == len(ids)                                   # unique
    for original, enc in zip(ids, encoded):
        assert ALLOWED_EXTERNAL.match(enc), enc                             # only [A-Za-z0-9_.:-]
        assert rf.decode_external_id(enc) == original                       # reversible
    assert rf.encode_external_id(ids[0]) == ids[0]                          # a UUID is unchanged
    assert rf.encode_external_id("a b") == "a:20b" and rf.encode_external_id(":") == ":3a"
    # the escape character itself is escaped, so "a:20b" (a literal colon id) cannot collide with "a b"
    assert rf.encode_external_id("a:20b") != rf.encode_external_id("a b")


def test_external_id_limits_and_foreign_values():
    with pytest.raises(ValueError):
        rf.encode_external_id("")
    with pytest.raises(ValueError):
        rf.encode_external_id("é" * 100)                                   # 100 * 6 chars > 255
    assert rf.safe_decode_external_id(None) is None and rf.safe_decode_external_id("") is None
    assert rf.safe_decode_external_id(":zz") is None                        # not written by this app
    assert rf.safe_decode_external_id("abc") == "abc"


# --------------------------------------------------------------------------- configuration
def test_region_collection_threshold_retries_from_env(monkeypatch):
    for name in ("REKOGNITION_REGION", "AWS_S3_REGION", "REKOGNITION_COLLECTION_ID", "REKOGNITION_THRESHOLD",
                 "REKOGNITION_MAX_RETRIES", "REKOGNITION_AUTO_CREATE"):
        monkeypatch.delenv(name, raising=False)
    assert rf.region() == "ap-south-1" and rf.collection_id() == "clockyn-faces"
    assert rf.match_threshold() == 90.0 and rf.max_retries() == 3 and rf.auto_create_enabled() is False
    monkeypatch.setenv("AWS_S3_REGION", "eu-west-1")
    assert rf.region() == "eu-west-1"
    monkeypatch.setenv("REKOGNITION_REGION", "us-east-1")
    assert rf.region() == "us-east-1"                                       # REKOGNITION_REGION wins
    monkeypatch.setenv("REKOGNITION_COLLECTION_ID", "my-faces")
    assert rf.collection_id() == "my-faces"
    monkeypatch.setenv("REKOGNITION_AUTO_CREATE", "yes")
    assert rf.auto_create_enabled() is True
    for raw, expected in (("85.5", 85.5), ("0", 0.0), ("100", 100.0), ("abc", 90.0), ("101", 90.0), ("-1", 90.0), ("nan", 90.0), ("", 90.0)):
        monkeypatch.setenv("REKOGNITION_THRESHOLD", raw)
        assert rf.match_threshold() == expected, raw
    for raw, expected in (("5", 5), ("0", 3), ("11", 3), ("x", 3)):
        monkeypatch.setenv("REKOGNITION_MAX_RETRIES", raw)
        assert rf.max_retries() == expected, raw


def test_build_client_uses_standard_retries_timeouts_and_region(monkeypatch):
    import boto3
    seen = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kw: seen.update(service=service, **kw) or "client")
    monkeypatch.delenv("REKOGNITION_REGION", raising=False)
    monkeypatch.setenv("AWS_S3_REGION", "ap-south-1")
    monkeypatch.setenv("REKOGNITION_MAX_RETRIES", "4")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert rf.build_client() == "client"
    cfg = seen["config"]
    assert seen["service"] == "rekognition" and seen["region_name"] == "ap-south-1"
    assert cfg.retries == {"mode": "standard", "max_attempts": 4}
    assert cfg.connect_timeout == 5 and cfg.read_timeout == 10
    assert "aws_access_key_id" not in seen                                   # default credential chain (IAM role)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    rf.build_client()
    assert seen["aws_access_key_id"] == "AKIAEXAMPLE" and seen["aws_secret_access_key"] == "secret"


# --------------------------------------------------------------------------- images
def _noise_image(w, h, seed=3):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


def _decode(data):
    return Image.open(io.BytesIO(data))


def test_fit_image_returns_a_small_jpeg_or_png_unchanged():
    jpg = jpeg_bytes(1, (400, 300))
    assert rf.fit_image_bytes(jpg) == jpg
    buf = io.BytesIO()
    _noise_image(200, 200).save(buf, "PNG")
    assert rf.fit_image_bytes(buf.getvalue()) == buf.getvalue()


def test_fit_image_re_encodes_a_photo_over_5mb_and_over_1920px():
    buf = io.BytesIO()
    _noise_image(3200, 2400).save(buf, "PNG")                                # noise: ~23 MB as PNG
    big = buf.getvalue()
    assert len(big) > rf.MAX_IMAGE_BYTES
    out = rf.fit_image_bytes(big)
    img = _decode(out)
    assert img.format == "JPEG" and len(out) <= rf.MAX_IMAGE_BYTES and max(img.size) <= rf.MAX_IMAGE_SIDE
    assert img.size[0] / img.size[1] == pytest.approx(3200 / 2400, abs=0.01)  # aspect ratio kept


def test_fit_image_steps_quality_then_size_down_to_the_byte_limit():
    buf = io.BytesIO()
    _noise_image(1500, 1500).save(buf, "PNG")
    out = rf.fit_image_bytes(buf.getvalue(), max_bytes=60_000, max_side=1920)
    assert len(out) <= 60_000 and _decode(out).format == "JPEG"
    assert _decode(out).size[0] < 1500                                      # had to scale as well as lower the quality
    with pytest.raises(rf.RekognitionError) as e:
        rf.fit_image_bytes(buf.getvalue(), max_bytes=200, max_side=1920)    # impossible
    assert e.value.kind == "image_too_large"


def test_fit_image_applies_exif_rotation_and_rejects_non_images():
    img = _noise_image(60, 30)
    exif = Image.Exif()
    exif[0x0112] = 6                                                         # displayed 30 wide x 60 tall
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    out = rf.fit_image_bytes(buf.getvalue())
    assert _decode(out).size == (30, 60)
    with pytest.raises(rf.RekognitionError) as e:
        rf.fit_image_bytes(b"not an image")
    assert e.value.kind == "invalid_image"
    rgba = io.BytesIO()
    Image.new("RGBA", (50, 50), (255, 0, 0, 128)).save(rgba, "PNG")
    assert _decode(rf.fit_image_bytes(rgba.getvalue())).format == "JPEG"       # not RGB/L: re-encoded as JPEG


# --------------------------------------------------------------------------- error mapping
class EndpointConnectionError(Exception):          # same class name as botocore's: mapped by name, no botocore needed
    pass


class NoCredentialsError(Exception):
    pass


@pytest.mark.parametrize("code,message,kind,retryable", [
    ("InvalidParameterException", "There are no faces in the image. Should be at least 1.", "no_face", False),
    ("InvalidParameterException", "1 validation error detected: collectionId pattern", "invalid_parameter", False),
    ("InvalidImageFormatException", "bad", "invalid_image", False),
    ("ImageTooLargeException", "big", "image_too_large", False),
    ("ResourceNotFoundException", "no collection", "collection_missing", False),
    ("ResourceAlreadyExistsException", "exists", "already_exists", False),
    ("ProvisionedThroughputExceededException", "slow down", "throttled", True),
    ("ThrottlingException", "slow down", "throttled", True),
    ("AccessDeniedException", "not allowed", "access_denied", False),
    ("UnrecognizedClientException", "bad token", "access_denied", False),
    ("ExpiredTokenException", "old", "access_denied", False),
    ("InternalServerError", "oops", "unavailable", True),
    ("ServiceUnavailableException", "oops", "unavailable", True),
    ("SomethingNew", "?", "unknown", False),
])
def test_error_mapping_table(code, message, kind, retryable):
    err = rf.map_exception(client_error(code, message), "SearchFacesByImage", "clockyn-faces", "ap-south-1")
    assert (err.kind, err.retryable) == (kind, retryable) and err.operation == "SearchFacesByImage"


def test_transport_and_credential_errors_are_mapped_by_class_name():
    err = rf.map_exception(EndpointConnectionError("no route"), "DetectFaces")
    assert err.kind == "unavailable" and err.retryable
    err = rf.map_exception(NoCredentialsError("none"), "IndexFaces")
    assert err.kind == "access_denied" and "credentials" in str(err)
    assert rf.map_exception(ValueError("x"), "IndexFaces").kind == "unknown"
    same = rf.RekognitionError("no_face", "m")
    assert rf.map_exception(same, "X") is same


def test_access_denied_message_names_the_missing_iam_action_and_resource():
    err = rf.map_exception(client_error("AccessDeniedException", "User is not authorized"), "IndexFaces", "clockyn-faces", "ap-south-1")
    assert "rekognition:IndexFaces" in str(err)
    assert "arn:aws:rekognition:ap-south-1:<ACCOUNT_ID>:collection/clockyn-faces" in str(err)
    assert "rekognition_setup.py policy" in str(err)
    err = rf.map_exception(client_error("AccessDeniedException", "x"), "DetectFaces", "clockyn-faces", "ap-south-1")
    assert "rekognition:DetectFaces" in str(err) and "on *" in str(err)
    assert "authorized" not in str(err)                                     # the AWS text (which can carry ARNs) is not echoed


# --------------------------------------------------------------------------- operations
def test_detect_faces_parses_boxes_and_treats_no_face_as_empty():
    rek, client = make_rek()
    client.details = [face_detail(0.1, 0.2, 0.3, 0.4, 98.0), face_detail(0.6, 0.6, 0.1, 0.1, 91.0)]
    faces = rek.detect_faces(b"img")
    assert [f.box for f in faces] == [(0.1, 0.2, 0.3, 0.4), (0.6, 0.6, 0.1, 0.1)]
    assert faces[0].confidence == 98.0 and faces[0].brightness == 80.0 and faces[0].pose["Yaw"] == 1.0
    assert client.calls_of("detect_faces")[0] == {"Image": {"Bytes": b"img"}, "Attributes": ["DEFAULT"]}
    client.script["detect_faces"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    assert rek.detect_faces(b"img") == []
    client.script["detect_faces"] = [{"FaceDetails": []}]
    assert rek.detect_faces(b"img") == []


def test_pick_primary_counts_only_significant_other_faces():
    big = rf.Face((0.2, 0.2, 0.4, 0.5), 99.0)
    twin = rf.Face((0.7, 0.2, 0.3, 0.4), 97.0)                              # 0.3 / 0.4 = 75 % of the width: a second person
    far = rf.Face((0.9, 0.9, 0.05, 0.05), 99.0)                             # passer-by in the background
    weak = rf.Face((0.7, 0.7, 0.3, 0.3), 60.0)                              # low confidence
    assert rf.pick_primary([]) == (None, 0)
    assert rf.pick_primary([far, big]) == (big, 0)                          # largest is the primary
    assert rf.pick_primary([big, twin])[1] == 1
    assert rf.pick_primary([big, far, weak])[1] == 0
    assert rf.pick_primary([big, twin, far, weak]) == (big, 1)


def test_index_face_request_and_result():
    rek, client = make_rek()
    out = rek.index_face(b"img", "user 1")
    assert out.face_id == "face-001" and out.reasons == []
    kw = client.calls_of("index_faces")[0]
    assert kw == {"CollectionId": "clockyn-test", "Image": {"Bytes": b"img"}, "ExternalImageId": "user:201",
                  "MaxFaces": 1, "QualityFilter": "AUTO", "DetectionAttributes": []}
    client.script["index_faces"] = [{"FaceRecords": [], "UnindexedFaces": [{"Reasons": ["LOW_BRIGHTNESS", "LOW_SHARPNESS"]}]}]
    out = rek.index_face(b"img", "u1")
    assert out.face_id is None and out.reasons == ["LOW_BRIGHTNESS", "LOW_SHARPNESS"]
    assert rf.reason_from_unindexed(out.reasons) == "too_dark"
    assert rf.reason_from_unindexed(["LOW_SHARPNESS"]) == "too_blurry"
    assert rf.reason_from_unindexed(["SMALL_BOUNDING_BOX", "LOW_BRIGHTNESS"]) == "too_small"
    assert rf.reason_from_unindexed(["EXTREME_POSE"]) is None and rf.reason_from_unindexed([]) is None


def test_search_request_threshold_ordering_and_foreign_faces():
    rek, client = make_rek(threshold=87.5)
    client.matches = [match_entry("u2", 91.0, "a"), match_entry("u1", 99.1, "b"), match_entry("???:zz", 95.0, "c")]
    matches = rek.search(b"img")
    assert [(m.user_id, m.similarity) for m in matches] == [("u1", 99.1), (None, 95.0), ("u2", 91.0)]
    kw = client.calls_of("search_faces_by_image")[0]
    assert kw == {"CollectionId": "clockyn-test", "Image": {"Bytes": b"img"}, "MaxFaces": 5,
                  "FaceMatchThreshold": 87.5, "QualityFilter": "AUTO"}
    monkey_rek = rf.RekognitionFace(client=client, collection="c")          # threshold from the environment (default 90)
    assert monkey_rek.threshold == rf.match_threshold()


def test_search_without_a_usable_face_raises_no_face():
    rek, client = make_rek()
    client.script["search_faces_by_image"] = [client_error("InvalidParameterException", "There are no faces in the image.")]
    with pytest.raises(rf.RekognitionError) as e:
        rek.search(b"img")
    assert e.value.kind == "no_face"


def test_delete_faces_chunks_of_4096_and_counts():
    rek, client = make_rek()
    ids = ["f%d" % i for i in range(5000)]
    client.faces = {i: "u" for i in ids}
    assert rek.delete_faces(ids) == 5000
    sizes = [len(kw["FaceIds"]) for kw in client.calls_of("delete_faces")]
    assert sizes == [4096, 904]
    assert rek.delete_faces([]) == 0 and rek.delete_faces(["", None]) == 0
    assert len(client.calls_of("delete_faces")) == 2                        # nothing sent for an empty list
    client.collection_exists = False
    assert rek.delete_faces(["x"]) == 0                                     # missing collection: nothing to delete, no error


# --------------------------------------------------------------------------- failure modes
def test_throttling_surfaces_as_a_retryable_error(monkeypatch):
    rek, client = make_rek()
    client.script["search_faces_by_image"] = [client_error("ProvisionedThroughputExceededException", "slow")]
    with pytest.raises(rf.RekognitionError) as e:
        rek.search(b"img")
    assert e.value.kind == "throttled" and e.value.retryable is True
    assert len(client.calls_of("search_faces_by_image")) == 1               # retries are botocore's job (Config), not repeated here


def test_access_denied_is_actionable(monkeypatch):
    rek, client = make_rek(collection="clockyn-faces")
    client.script["index_faces"] = [client_error("AccessDeniedException", "nope")]
    with pytest.raises(rf.RekognitionError) as e:
        rek.index_face(b"img", "u1")
    assert e.value.kind == "access_denied" and "rekognition:IndexFaces" in str(e.value)


def test_missing_collection_without_auto_create_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("REKOGNITION_AUTO_CREATE", raising=False)
    rek, client = make_rek(collection="clockyn-faces")
    client.collection_exists = False
    with pytest.raises(rf.RekognitionError) as e:
        rek.index_face(b"img", "u1")
    assert e.value.kind == "collection_missing"
    assert "clockyn-faces" in str(e.value) and "rekognition_setup.py create" in str(e.value) and "REKOGNITION_AUTO_CREATE" in str(e.value)
    assert "create_collection" not in client.names()


def test_missing_collection_with_auto_create_creates_it_and_retries_once(monkeypatch):
    monkeypatch.setenv("REKOGNITION_AUTO_CREATE", "1")
    rek, client = make_rek(collection="clockyn-faces")
    client.collection_exists = False
    out = rek.index_face(b"img", "u1")
    assert out.face_id == "face-001"
    assert client.names() == ["index_faces", "create_collection", "index_faces"]
    assert client.calls_of("create_collection")[0] == {"CollectionId": "clockyn-faces"}
    # creation is not allowed for the app role: the access error is what the operator sees
    rek2, client2 = make_rek(collection="clockyn-faces")
    client2.collection_exists = False
    client2.script["create_collection"] = [client_error("AccessDeniedException", "no")]
    with pytest.raises(rf.RekognitionError) as e:
        rek2.index_face(b"img", "u1")
    assert e.value.kind == "access_denied" and "CreateCollection" in str(e.value)


def test_create_collection_reports_whether_it_was_new():
    rek, client = make_rek()
    assert rek.create_collection() is True
    client.script["create_collection"] = [client_error("ResourceAlreadyExistsException", "there")]
    assert rek.create_collection() is False
    assert rek.describe_collection()["FaceCount"] == 0


# --------------------------------------------------------------------------- logging hygiene
def test_no_image_bytes_in_logs_even_on_failures(caplog):
    caplog.set_level(logging.DEBUG)
    marker = b"SECRET-IMAGE-BYTES-0123456789"
    rek, client = make_rek()
    rek.detect_faces(marker)
    rek.index_face(marker, "u1")
    client.matches = [match_entry("u1", 99.0)]
    rek.search(marker)
    client.script["search_faces_by_image"] = [client_error("AccessDeniedException", "denied")]
    with pytest.raises(rf.RekognitionError):
        rek.search(marker)
    client.script["index_faces"] = [client_error("ThrottlingException", "slow")]
    with pytest.raises(rf.RekognitionError):
        rek.index_face(marker, "u1")
    text = caplog.text
    assert "SECRET-IMAGE-BYTES" not in text and repr(marker) not in text and marker.hex() not in text
    assert caplog.records, "failures are logged (operation + kind only)"
