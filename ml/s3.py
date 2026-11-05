import logging
import os
import uuid
import boto3
import numpy as np
from dotenv import load_dotenv
from typing import List
from utils.uploads import HEIC_CONTENT_TYPE, HEIC_EXT, is_heic

load_dotenv()
logger = logging.getLogger(__name__)

# Empty / unset keys fall back to boto3's default credential chain (IAM role on App Runner / ECS).
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID") or None
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or None
AWS_S3_REGION = os.getenv("AWS_S3_REGION")
AWS_REGION = AWS_S3_REGION  # alias for backward compatibility
S3_BUCKET = os.getenv("S3_BUCKET_NAME")

KNOWN_FACES_PREFIX = "known_faces"

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION,
)


def user_prefix(user_uuid: str) -> str:
    return f"{KNOWN_FACES_PREFIX}/{user_uuid}/"


# LEGACY (local-model design): known_faces/<user>/*.npy embeddings. New enrolments no longer write them (faces live in the
# Rekognition collection, ml/face_service.py); these helpers are kept for the S3 cleanup of old data and a possible
# one-off tool. Deleting a user still removes any leftover known_faces/ objects (delete_employee_s3_assets).
def save_face_embeddings(
    user_uuid: str,
    embeddings: List[np.ndarray],
    max_faces: int = 4
):
    prefix = user_prefix(user_uuid)

    # Delete old embeddings
    resp = s3.list_objects_v2(
        Bucket=S3_BUCKET,
        Prefix=prefix
    )

    if "Contents" in resp:
        for obj in resp["Contents"]:
            if obj["Key"].endswith(".npy"):
                s3.delete_object(
                    Bucket=S3_BUCKET,
                    Key=obj["Key"]
                )

    count = 0

    for emb in embeddings:
        if emb is None or count >= max_faces:
            continue

        key = f"{prefix}{uuid.uuid4().hex}.npy"

        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=emb.astype(np.float32).tobytes(),
            ContentType="application/octet-stream"
        )

        count += 1

    if count == 0:
        raise Exception("No embeddings saved")


def load_face_embeddings(user_uuid: str) -> List[np.ndarray]:
    prefix = user_prefix(user_uuid)

    resp = s3.list_objects_v2(
        Bucket=S3_BUCKET,
        Prefix=prefix
    )

    embeddings = []

    if "Contents" not in resp:
        return embeddings

    for obj in resp["Contents"]:
        if not obj["Key"].endswith(".npy"):
            continue

        data = s3.get_object(
            Bucket=S3_BUCKET,
            Key=obj["Key"]
        )["Body"].read()

        emb = np.frombuffer(data, dtype=np.float32)
        embeddings.append(emb)

    return embeddings


def list_all_users() -> List[str]:
    resp = s3.list_objects_v2(
        Bucket=S3_BUCKET,
        Prefix=f"{KNOWN_FACES_PREFIX}/",
        Delimiter="/"
    )

    users = []

    if "CommonPrefixes" in resp:
        for p in resp["CommonPrefixes"]:
            users.append(p["Prefix"].split("/")[1])

    return users


def put_image_variant(key_stem: str, data: bytes, default_ext: str, default_content_type: str) -> str:
    """Store an uploaded image under `<key_stem>.<default_ext>`; HEIC / HEIF bytes (older iPhone builds) are stored as
    received under `<key_stem>.heic` with content type image/heic (no server-side conversion) and the stale
    `<default_ext>` object is removed so the readers (which look at `<default_ext>` first) do not keep serving the old
    picture. Returns the key written."""
    if is_heic(data):
        key = f"{key_stem}.{HEIC_EXT}"
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=HEIC_CONTENT_TYPE)
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=f"{key_stem}.{default_ext}")
        except Exception as e:
            logger.warning("Could not remove the previous %s image for %s: %s", default_ext, key_stem, e)
        return key
    key = f"{key_stem}.{default_ext}"
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=default_content_type)
    return key


def save_org_logo(org_id: str, logo_bytes: bytes) -> str:
    """Save organization logo to S3 and return a fresh presigned URL"""
    key = put_image_variant(f"org_logo/{org_id}", logo_bytes, "png", "image/png")

    return s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=604800
    )


def get_org_logo_url(org_id: str) -> str:
    """Get organization logo URL from S3, return None if not exists"""
    for ext in ("png", HEIC_EXT):                  # png first; heic = an older iPhone build uploaded a HEIC logo
        key = f"org_logo/{org_id}.{ext}"
        try:
            # Check if object exists
            s3.head_object(Bucket=S3_BUCKET, Key=key)
            # Generate presigned URL for private bucket (7 days expiration)
            url = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': key},
                ExpiresIn=604800
            )
            return url
        except Exception as e:
            logger.debug(f"No logo found for {key}: {str(e)}")
    return None


def save_employee_profile(user_id: str, photo_bytes: bytes, content_type: str = "image/jpeg") -> str:
    """Save employee profile photo to S3 and return a fresh presigned URL"""
    key = put_image_variant(f"employee_profile/{user_id}", photo_bytes, "jpg", content_type)
    return s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=604800
    )


def get_employee_profile_url(user_id: str) -> str:
    """Get employee profile photo URL from S3, return None if not exists"""
    for ext in ['jpg', 'png', HEIC_EXT]:
        key = f"employee_profile/{user_id}.{ext}"
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=key)
            return s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': key},
                ExpiresIn=604800
            )
        except:
            continue
    return None


# ── Punch image storage ──
PUNCH_PHOTO_PREFIX = "empolyee-punch-photos"  # intentional typo matches existing S3 path


def save_punch_image(user_id: str, org_id: str, attendance_id: int, action: str, image_bytes: bytes) -> str:
    """
    Save a punch-in or punch-out face image to S3 (bucket from S3_BUCKET_NAME).
    Returns a presigned URL (7 days) on success, None on failure.
    """
    key = f"{PUNCH_PHOTO_PREFIX}/{org_id}/{user_id}/{attendance_id}_{action}.jpg"
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=image_bytes,
            ContentType="image/jpeg"
        )
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=604800  # 7 days
        )
        print(f"Punch image saved: {key}")
        return url
    except Exception as e:
        print(f"Failed to save punch image: {str(e)}")
        return None


def get_punch_image_url(org_id: str, user_id: str, attendance_id: int, action: str, expires_in: int = 604800) -> str:
    """
    Get a fresh presigned URL for a stored punch image.
    Returns None if not found.
    """
    key = f"{PUNCH_PHOTO_PREFIX}/{org_id}/{user_id}/{attendance_id}_{action}.jpg"
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=key)
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=expires_in
        )
    except Exception as e:
        print(f"Punch image not found or error: {str(e)}")
        return None


def upload_chat_file(file_bytes: bytes, filename: str, content_type: str) -> tuple:
    """Upload a chat attachment to S3 documents/ prefix.
    Returns (s3_key, presigned_url) — store the key in DB, serve the presigned URL.
    """
    if is_heic(file_bytes):                      # HEIC photo from an older iPhone build: store as received, labelled honestly
        content_type = HEIC_CONTENT_TYPE
        if not (filename or "").lower().endswith((".heic", ".heif")):
            filename = f"{os.path.splitext(filename or 'image')[0]}.{HEIC_EXT}"
    key = f"documents/{uuid.uuid4().hex}_{filename}"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    presigned_url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=604800
    )
    return key, presigned_url


def get_chat_file_presigned_url(s3_key: str, expires_in: int = 604800) -> str:
    """Generate a fresh presigned URL for a stored chat file key.
    Handles both raw S3 keys and legacy stored presigned/public URLs.
    """
    if not s3_key:
        return None
    try:
        # If it looks like a full URL (legacy rows stored presigned URL in file_url),
        # extract the key from the path component.
        if s3_key.startswith('http'):
            from urllib.parse import urlparse
            parsed = urlparse(s3_key)
            key = parsed.path.lstrip('/')
            # Path may be /<bucket>/<key> on path-style URLs, or just /<key> on virtual-hosted
            # Strip bucket name prefix if present
            bucket = S3_BUCKET or ''
            if key.startswith(bucket + '/'):
                key = key[len(bucket) + 1:]
            if not key:
                return s3_key  # can't parse, return original
        else:
            key = s3_key

        # Generate presigned URL directly — skip head_object to avoid 403/404 on valid keys
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=expires_in
        )
    except Exception as e:
        print(f"Chat file presigned URL error: {s3_key} — {e}")
        return None


def upload_support_photo(file_bytes: bytes, filename: str, content_type: str) -> tuple:
    """Upload a support photo to S3 client_support/ prefix.
    Returns (s3_key, presigned_url).
    """
    if is_heic(file_bytes):
        content_type = HEIC_CONTENT_TYPE
        if not (filename or "").lower().endswith((".heic", ".heif")):
            filename = f"{os.path.splitext(filename or 'image')[0]}.{HEIC_EXT}"
    key = f"client_support/{uuid.uuid4().hex}_{filename}"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    presigned_url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=604800
    )
    return key, presigned_url


def get_support_photo_url(s3_key: str, expires_in: int = 604800) -> str:
    """Generate a fresh presigned URL for a stored support photo key."""
    if not s3_key:
        return None
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expires_in
        )
    except Exception as e:
        print(f"Support photo not found: {s3_key} — {e}")
        return None


def _delete_prefix(prefix: str) -> int:
    """Delete every object under an S3 prefix. Returns count deleted."""
    deleted = 0
    continuation_token = None
    while True:
        kwargs = {"Bucket": S3_BUCKET, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        resp = s3.list_objects_v2(**kwargs)
        contents = resp.get("Contents", [])
        if contents:
            objects = [{"Key": obj["Key"]} for obj in contents]
            s3.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": objects})
            deleted += len(objects)
        if resp.get("IsTruncated"):
            continuation_token = resp.get("NextContinuationToken")
        else:
            break
    return deleted


def _extract_s3_key(stored_value: str):
    """Extract a raw S3 key from either a raw key or a stored (possibly presigned) URL."""
    if not stored_value:
        return None
    if stored_value.startswith('http'):
        from urllib.parse import urlparse
        key = urlparse(stored_value).path.lstrip('/')
        bucket = S3_BUCKET or ''
        if key.startswith(bucket + '/'):
            key = key[len(bucket) + 1:]
        return key or None
    return stored_value


def delete_employee_s3_assets(user_id: str, extra_urls: List[str] = None) -> dict:
    """
    Best-effort PERMANENT deletion of every S3 object tied to one employee:
      - known_faces/{user_id}/*        (legacy face embeddings; Rekognition faces are removed by ml/face_service.remove)
      - employee_profile/{user_id}.*   (profile photo)
      - any punch-photo / chat-file URLs passed in via extra_urls (harvested
        from the DB by the caller before those rows were deleted)

    Only call this AFTER the employee's database rows have already been
    committed as deleted — this function never raises (it logs and
    continues), since by this point the DB deletion is final and a storage
    cleanup miss is a recoverable, low-severity issue, unlike losing
    already-confirmed database state to an S3 error.
    """
    import logging
    logger = logging.getLogger(__name__)
    summary = {"known_faces": 0, "profile_photos": 0, "other": 0, "errors": []}

    try:
        summary["known_faces"] = _delete_prefix(user_prefix(user_id))
    except Exception as e:
        summary["errors"].append(f"known_faces: {e}")

    for ext in ("jpg", "png"):
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=f"employee_profile/{user_id}.{ext}")
            summary["profile_photos"] += 1
        except Exception as e:
            summary["errors"].append(f"employee_profile.{ext}: {e}")

    for url in (extra_urls or []):
        key = _extract_s3_key(url)
        if not key:
            continue
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
            summary["other"] += 1
        except Exception as e:
            summary["errors"].append(f"{key}: {e}")

    if summary["errors"]:
        logger.error(f"S3 cleanup for deleted employee {user_id} had errors: {summary['errors']}")
    return summary


def refresh_punch_image_url(stored_url: str, expires_in: int = 604800) -> str:
    """
    Given a previously stored URL (presigned or public), extract the S3 key
    and return a fresh presigned URL. Returns the original URL if key can't be parsed.
    """
    if not stored_url:
        return stored_url
    try:
        # Extract key from either presigned URL or direct S3 URL
        # Format: https://<bucket>.s3.<region>.amazonaws.com/<key>?...
        from urllib.parse import urlparse
        parsed = urlparse(stored_url)
        key = parsed.path.lstrip('/')
        if not key.startswith(PUNCH_PHOTO_PREFIX):
            return stored_url
        s3.head_object(Bucket=S3_BUCKET, Key=key)
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=expires_in
        )
    except Exception:
        return stored_url