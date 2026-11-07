"""DB-free tests of the Video Hub helpers (core_functions/support_video_functions.py).
Run: python -m pytest tests/test_support_videos.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions.support_video_functions import normalize_s3_key  # noqa: E402


def test_bare_key_is_unchanged():
    assert normalize_s3_key("guide_videos/a.mp4", "bkt") == "guide_videos/a.mp4"


def test_leading_slash_and_whitespace_are_dropped():
    assert normalize_s3_key("  /guide_videos/a.mp4 ", "bkt") == "guide_videos/a.mp4"


def test_s3_uri_of_our_bucket_gives_the_key():
    assert normalize_s3_key("s3://bkt/guide_videos/a.mp4", "bkt") == "guide_videos/a.mp4"


def test_empty_gives_none():
    assert normalize_s3_key("", "bkt") is None
    assert normalize_s3_key(None, "bkt") is None
