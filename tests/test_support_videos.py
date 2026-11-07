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


def test_safe_filename_strips_directories_and_odd_characters():
    from core_functions.support_video_functions import safe_filename
    assert safe_filename(r"C:\Users\me\My Guide (1).MP4") == "My_Guide_1.mp4"
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("") == "video"
    assert safe_filename("noext") == "noext"


def test_new_video_key_is_below_the_guide_prefix_and_unique():
    from core_functions.support_video_functions import GUIDE_VIDEO_PREFIX, is_guide_video_key, new_video_key
    a, b = new_video_key("a.mp4"), new_video_key("a.mp4")
    assert a != b
    assert a.startswith(GUIDE_VIDEO_PREFIX) and a.endswith("_a.mp4")
    assert is_guide_video_key(a)


def test_only_keys_below_the_guide_prefix_are_accepted():
    from core_functions.support_video_functions import is_guide_video_key
    assert is_guide_video_key("guide_videos/x.mp4")
    assert not is_guide_video_key("guide_videos/")
    assert not is_guide_video_key("known_faces/u1/x.npy")
    assert not is_guide_video_key("guide_videos/../known_faces/x")
    assert not is_guide_video_key(None)
