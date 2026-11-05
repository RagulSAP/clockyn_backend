"""Fakes shared by the Rekognition tests (no AWS, no database): a fake boto3 `rekognition` client, an in-memory
user_face_index store, and small image builders."""
import io

import numpy as np
from botocore.exceptions import ClientError
from PIL import Image


def client_error(code, message="boom", operation="Op"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def jpeg_bytes(seed=1, size=(320, 320), quality=85):
    rng = np.random.default_rng(seed)
    arr = rng.integers(40, 220, (size[1], size[0], 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def face_detail(left=0.3, top=0.2, width=0.4, height=0.5, confidence=99.5):
    return {
        "BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": height},
        "Confidence": confidence,
        "Quality": {"Brightness": 80.0, "Sharpness": 90.0},
        "Pose": {"Roll": 0.0, "Yaw": 1.0, "Pitch": 2.0},
    }


class FakeRekognitionClient:
    """Records every call as (method, kwargs). `script[method]` is a list consumed one entry per call: a dict is the
    response, an Exception instance is raised. When the script is empty the default behaviour applies."""

    def __init__(self):
        self.calls = []
        self.script = {}
        self.faces = {}                       # face_id -> ExternalImageId
        self.counter = 0
        self.details = [face_detail()]        # default DetectFaces answer: exactly one face
        self.matches = []                     # default SearchFacesByImage FaceMatches
        self.collection_exists = True

    # -- helpers
    def names(self):
        return [name for name, _kw in self.calls]

    def calls_of(self, name):
        return [kw for n, kw in self.calls if n == name]

    def _scripted(self, name):
        queue = self.script.get(name)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return None

    def _check_collection(self):
        if not self.collection_exists:
            raise client_error("ResourceNotFoundException", "collection missing")

    # -- API
    def detect_faces(self, **kw):
        self.calls.append(("detect_faces", kw))
        scripted = self._scripted("detect_faces")
        if scripted is not None:
            return scripted
        return {"FaceDetails": list(self.details)}

    def index_faces(self, **kw):
        self.calls.append(("index_faces", kw))
        scripted = self._scripted("index_faces")
        if scripted is not None:
            return scripted
        self._check_collection()
        self.counter += 1
        face_id = "face-%03d" % self.counter
        self.faces[face_id] = kw.get("ExternalImageId")
        return {"FaceRecords": [{"Face": {"FaceId": face_id, "Confidence": 99.9, "ExternalImageId": kw.get("ExternalImageId")}}],
                "UnindexedFaces": []}

    def search_faces_by_image(self, **kw):
        self.calls.append(("search_faces_by_image", kw))
        scripted = self._scripted("search_faces_by_image")
        if scripted is not None:
            return scripted
        self._check_collection()
        return {"FaceMatches": list(self.matches)}

    def delete_faces(self, **kw):
        self.calls.append(("delete_faces", kw))
        scripted = self._scripted("delete_faces")
        if scripted is not None:
            return scripted
        self._check_collection()
        deleted = [f for f in kw["FaceIds"] if f in self.faces]
        for f in deleted:
            del self.faces[f]
        return {"DeletedFaces": deleted}

    def create_collection(self, **kw):
        self.calls.append(("create_collection", kw))
        scripted = self._scripted("create_collection")
        if scripted is not None:
            return scripted
        self.collection_exists = True
        return {"StatusCode": 200, "CollectionArn": "arn:aws:rekognition:ap-south-1:1:collection/" + kw["CollectionId"]}

    def describe_collection(self, **kw):
        self.calls.append(("describe_collection", kw))
        scripted = self._scripted("describe_collection")
        if scripted is not None:
            return scripted
        self._check_collection()
        return {"FaceCount": len(self.faces), "FaceModelVersion": "7.0",
                "CollectionARN": "arn:aws:rekognition:ap-south-1:1:collection/" + kw["CollectionId"]}

    def list_faces(self, **kw):
        self.calls.append(("list_faces", kw))
        scripted = self._scripted("list_faces")
        if scripted is not None:
            return scripted
        self._check_collection()
        return {"Faces": []}

    def list_collections(self, **kw):
        self.calls.append(("list_collections", kw))
        scripted = self._scripted("list_collections")
        if scripted is not None:
            return scripted
        return {"CollectionIds": ["clockyn-faces"]}


def match_entry(user_id_encoded, similarity, face_id="m-face"):
    return {"Similarity": similarity, "Face": {"FaceId": face_id, "ExternalImageId": user_id_encoded, "Confidence": 99.0}}


class FakeStore:
    """In-memory user_face_index with failure injection."""

    def __init__(self):
        self.rows = []                       # (user_id, face_id, photo_no)
        self.fail_add = False
        self.fail_list = False
        self.fail_delete = False
        self.events = []

    def seed(self, user_id, face_ids):
        for n, fid in enumerate(face_ids, 1):
            self.rows.append((user_id, fid, n))

    def ids_of(self, user_id):
        return [fid for uid, fid, _n in self.rows if uid == user_id]

    def list_face_ids(self, user_id):
        self.events.append("list")
        if self.fail_list:
            raise RuntimeError("db down (list)")
        return self.ids_of(user_id)

    def has_faces(self, user_id):
        return bool(self.ids_of(user_id))

    def add_faces(self, user_id, items):
        self.events.append("add")
        if self.fail_add:
            raise RuntimeError("db down (insert)")
        for face_id, photo_no in items:
            self.rows.append((user_id, face_id, photo_no))

    def delete_face_ids(self, face_ids):
        self.events.append("delete")
        if self.fail_delete:
            raise RuntimeError("db down (delete)")
        wanted = set(face_ids)
        before = len(self.rows)
        self.rows = [r for r in self.rows if r[1] not in wanted]
        return before - len(self.rows)
