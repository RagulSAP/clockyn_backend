import logging
from typing import Optional
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)


def normalize_s3_key(s3_path: Optional[str], bucket: Optional[str] = None) -> Optional[str]:
    """`support_video.s3_path` may be a bare key (`guide_videos/a.mp4`) or an `s3://bucket/key` URI: returns the key."""
    path = (s3_path or "").strip()
    if path.startswith("s3://"):
        path = path[len("s3://"):]
        head, _, rest = path.partition("/")
        path = rest if (bucket is None or head == bucket) else path
    path = path.lstrip("/")
    return path or None


async def get_active_support_videos():
    """Active (status = 1) guide videos, oldest first, each with a presigned playback URL."""
    from ml.s3 import S3_BUCKET, get_support_video_url
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, video_name, s3_path FROM support_video WHERE status = 1 ORDER BY id")
            rows = cur.fetchall()
    finally:
        return_connection(conn)

    videos = []
    for row in rows:
        key = normalize_s3_key(row["s3_path"], S3_BUCKET)
        try:
            url = get_support_video_url(key)
        except Exception:
            logger.exception("Could not presign guide video %s", row["id"])
            continue
        if url:
            videos.append({"id": row["id"], "name": row["video_name"], "url": url})
    return videos
