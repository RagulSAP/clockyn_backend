import logging
import os
import re
import uuid
from typing import Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

# Every guide video lives under this prefix: the admin routes only register / delete keys below it, so they can never
# be used to presign or delete another object of the bucket (face photos, invoices, chat files ...).
GUIDE_VIDEO_PREFIX = "guide_videos/"
MAX_VIDEO_NAME = 150
DEFAULT_VIEW_LIMIT = 1000
MAX_VIEW_LIMIT = 5000


class VideoUploadUrlRequest(BaseModel):
    filename: str
    content_type: str


class VideoCreateRequest(BaseModel):
    video_name: str
    s3_path: str


class VideoUpdateRequest(BaseModel):
    video_name: Optional[str] = None
    status: Optional[int] = None


class VideoViewRequest(BaseModel):
    user_id: str
    org_id: str


# ---------------------------------------------------------------------------------------------- pure helpers
def normalize_s3_key(s3_path: Optional[str], bucket: Optional[str] = None) -> Optional[str]:
    """`support_video.s3_path` may be a bare key (`guide_videos/a.mp4`) or an `s3://bucket/key` URI: returns the key."""
    path = (s3_path or "").strip()
    if path.startswith("s3://"):
        path = path[len("s3://"):]
        head, _, rest = path.partition("/")
        path = rest if (bucket is None or head == bucket) else path
    path = path.lstrip("/")
    return path or None


def safe_filename(filename: Optional[str]) -> str:
    """A file name that is safe inside an S3 key: no directories, only [A-Za-z0-9._-], extension kept."""
    base = os.path.basename((filename or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:60] or "video"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8].lower()
    return f"{stem}.{ext}" if ext else stem


def new_video_key(filename: Optional[str]) -> str:
    return f"{GUIDE_VIDEO_PREFIX}{uuid.uuid4().hex}_{safe_filename(filename)}"


def is_guide_video_key(key: Optional[str]) -> bool:
    return bool(key) and key.startswith(GUIDE_VIDEO_PREFIX) and ".." not in key and len(key) > len(GUIDE_VIDEO_PREFIX)


def clean_video_name(name: Optional[str]) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Video name is required")
    if len(cleaned) > MAX_VIDEO_NAME:
        raise HTTPException(status_code=400, detail=f"Video name must be at most {MAX_VIDEO_NAME} characters")
    return cleaned


def _bucket() -> Optional[str]:
    from ml.s3 import S3_BUCKET
    return S3_BUCKET


# ---------------------------------------------------------------------------------------------- app (Video Hub)
async def get_active_support_videos():
    """Active (status = 1) guide videos, oldest first, each with a presigned playback URL."""
    from ml.s3 import get_support_video_url
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, video_name, s3_path FROM support_video WHERE status = 1 ORDER BY id")
            rows = cur.fetchall()
    finally:
        return_connection(conn)

    videos = []
    for row in rows:
        key = normalize_s3_key(row["s3_path"], _bucket())
        try:
            url = get_support_video_url(key)
        except Exception:
            logger.exception("Could not presign guide video %s", row["id"])
            continue
        if url:
            videos.append({"id": row["id"], "name": row["video_name"], "url": url})
    return videos


async def record_video_view(video_id: int, user_id: str, org_id: str):
    """The app opened an ACTIVE guide video: one row per tap (who / which organisation / which video)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, video_name FROM support_video WHERE id = %s AND status = 1", (video_id,))
            video = cur.fetchone()
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
            cur.execute(
                "INSERT INTO support_video_view (video_id, video_name, user_id, org_id) VALUES (%s, %s, %s, %s)",
                (video["id"], video["video_name"], user_id, org_id),
            )
        conn.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Could not record video view")
        raise HTTPException(status_code=500, detail="Could not record the view")
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------------------------- operator (admin web app)
async def list_all_support_videos():
    """Every guide video, any status, with its view count and a preview link."""
    from ml.s3 import get_support_video_url
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.id, s.video_name, s.s3_path, s.status, s.created_at, COUNT(v.id) AS view_count
                FROM support_video s
                LEFT JOIN support_video_view v ON v.video_id = s.id
                GROUP BY s.id
                ORDER BY s.id
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        return_connection(conn)
    for row in rows:
        try:
            row["url"] = get_support_video_url(normalize_s3_key(row["s3_path"], _bucket()))
        except Exception:
            logger.exception("Could not presign guide video %s", row["id"])
            row["url"] = None
    return rows


async def create_video_upload_url(data: VideoUploadUrlRequest):
    """Step 1 of an upload: a presigned PUT URL and the key the file will have."""
    from ml.s3 import create_support_video_upload_url
    content_type = (data.content_type or "").strip().lower()
    if not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Only video files can be uploaded")
    key = new_video_key(data.filename)
    try:
        url = create_support_video_upload_url(key, content_type)
    except Exception:
        logger.exception("Could not presign a guide video upload")
        raise HTTPException(status_code=500, detail="Could not prepare the upload")
    return {"success": True, "upload_url": url, "s3_path": key, "content_type": content_type}


async def create_support_video(data: VideoCreateRequest):
    """Step 2 of an upload: register the uploaded file (active)."""
    name = clean_video_name(data.video_name)
    key = normalize_s3_key(data.s3_path, _bucket())
    if not is_guide_video_key(key):
        raise HTTPException(status_code=400, detail=f"s3_path must be below {GUIDE_VIDEO_PREFIX}")
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO support_video (video_name, s3_path, status) VALUES (%s, %s, 1) "
                "RETURNING id, video_name, s3_path, status, created_at",
                (name, key),
            )
            video = dict(cur.fetchone())
        conn.commit()
        return {"success": True, "video": video}
    except Exception:
        conn.rollback()
        logger.exception("Could not register the guide video")
        raise HTTPException(status_code=500, detail="Could not save the video")
    finally:
        return_connection(conn)


async def update_support_video(video_id: int, data: VideoUpdateRequest):
    """Rename and / or switch the status (1 = shown in the app, 0 = hidden)."""
    sets, params = [], []
    if data.video_name is not None:
        sets.append("video_name = %s")
        params.append(clean_video_name(data.video_name))
    if data.status is not None:
        if data.status not in (0, 1):
            raise HTTPException(status_code=400, detail="status must be 0 or 1")
        sets.append("status = %s")
        params.append(data.status)
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE support_video SET {', '.join(sets)} WHERE id = %s", (*params, video_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Video not found")
        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Could not update the guide video")
        raise HTTPException(status_code=500, detail="Could not update the video")
    finally:
        return_connection(conn)


async def delete_support_video(video_id: int):
    """Delete the row and (best effort) the file. The view history stays: it keeps the video name."""
    from ml.s3 import delete_s3_object
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("DELETE FROM support_video WHERE id = %s RETURNING s3_path", (video_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Video not found")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Could not delete the guide video")
        raise HTTPException(status_code=500, detail="Could not delete the video")
    finally:
        return_connection(conn)

    key = normalize_s3_key(row["s3_path"], _bucket())
    if is_guide_video_key(key):
        try:
            delete_s3_object(key)
        except Exception:
            logger.exception("Guide video row %s deleted but its file %s could not be removed from S3", video_id, key)
    return {"success": True}


async def list_support_video_views(video_id: Optional[int] = None, limit: int = DEFAULT_VIEW_LIMIT):
    """Newest first: which user of which organisation opened which video (names joined at read time)."""
    limit = max(1, min(int(limit or DEFAULT_VIEW_LIMIT), MAX_VIEW_LIMIT))
    where, params = "", []
    if video_id is not None:
        where = "WHERE v.video_id = %s"
        params.append(video_id)
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT v.id, v.video_id, v.video_name, v.user_id, v.org_id, v.viewed_at,
                       u.name AS user_name, u.email AS user_email, u.mobile_number AS user_mobile, u.role_id,
                       o.org_name
                FROM support_video_view v
                LEFT JOIN users u ON u.user_id = v.user_id
                LEFT JOIN org_info o ON o.org_id = v.org_id
                {where}
                ORDER BY v.viewed_at DESC, v.id DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        return_connection(conn)
