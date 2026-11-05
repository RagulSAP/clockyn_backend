"""Bounded, type-checked reading of uploaded files (WP12).

`read_upload(file, max_mb, allowed_types)` streams the upload in chunks and stops as soon as the limit is crossed
(413), so an oversized body is never held in memory in full; the type is decided from the FIRST BYTES of the file
(magic numbers), never from the client-supplied `Content-Type` or file name (415).

  max_mb         limit in megabytes; None = `UPLOAD_MAX_MB` (default 10). Chat attachments use `chat_upload_max_mb()`
                 (`CHAT_UPLOAD_MAX_MB`, default 25).
  allowed_types  an iterable of kinds out of png / jpeg / webp / gif / pdf (see IMAGES, IMAGES_AND_PDF), or None for
                 "any content" (only the size limit applies; used for chat attachments, which may be documents).
  allow_heic     also accept HEIC / HEIF (ISO `ftyp` box, brands in HEIC_BRANDS). Set by the generic image endpoints
                 (profile photo, logos, expense receipts, support); never by the face endpoints.

`check_upload(file, ...)` does the same but rewinds the file afterwards, for code that still reads the UploadFile itself
(face enrolment / punch). Invalid values in the environment fall back to the defaults.
"""
import logging
import os
from typing import Iterable, Optional

from fastapi import HTTPException

logger = logging.getLogger("uploads")

DEFAULT_MAX_MB = 10.0
DEFAULT_CHAT_MAX_MB = 25.0
CHUNK_SIZE = 64 * 1024

IMAGES = ("png", "jpeg", "webp", "gif")
IMAGES_AND_PDF = IMAGES + ("pdf",)
SNIFF_BYTES = 16
# HEIC / HEIF: bytes 4-8 are b"ftyp", bytes 8-12 the major brand. Only image brands (not mp42 / isom / qt ...).
HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
HEIC_EXT = "heic"
HEIC_CONTENT_TYPE = "image/heic"


def _env_mb(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number: using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r must be positive: using %s", name, raw, default)
        return default
    return value


def upload_max_mb() -> float:
    return _env_mb("UPLOAD_MAX_MB", DEFAULT_MAX_MB)


def chat_upload_max_mb() -> float:
    return _env_mb("CHAT_UPLOAD_MAX_MB", DEFAULT_CHAT_MAX_MB)


def sniff_type(head: bytes) -> Optional[str]:
    """png / jpeg / webp / gif / pdf from the first bytes of a file, else None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in HEIC_BRANDS:
        return "heic"
    return None


def is_heic(data: bytes) -> bool:
    """True when `data` starts like a HEIC / HEIF image (ISO base media `ftyp` box with an image brand)."""
    return sniff_type(bytes(data[:SNIFF_BYTES])) == "heic"


def _too_large(limit_mb: float) -> HTTPException:
    shown = int(limit_mb) if float(limit_mb).is_integer() else limit_mb
    return HTTPException(status_code=413, detail="File is too large (maximum %s MB)" % shown)


def _unsupported(allowed) -> HTTPException:
    return HTTPException(status_code=415, detail="Unsupported file type. Allowed: %s" % ", ".join(allowed))


def _allowed_kinds(allowed_types: Optional[Iterable[str]], allow_heic: bool) -> Optional[tuple]:
    """`allowed_types` as a tuple (None = any content), plus "heic" when `allow_heic` (older iPhone builds send HEIC)."""
    if allowed_types is None:
        return None
    allowed = tuple(allowed_types)
    if allow_heic and "heic" not in allowed:
        allowed += ("heic",)
    return allowed


async def read_upload(file, max_mb: Optional[float] = None, allowed_types: Optional[Iterable[str]] = IMAGES,
                      *, allow_empty: bool = False, allow_heic: bool = False, chunk_size: int = CHUNK_SIZE) -> bytes:
    """The bytes of an UploadFile. 413 over the limit, 415 for a type outside `allowed_types`, 400 for an empty file
    (unless `allow_empty`, which returns b"" and skips the type check). `allow_heic` additionally accepts HEIC / HEIF
    (generic image endpoints only; the face endpoints never set it: the ML decoder cannot read HEIC)."""
    limit_mb = float(max_mb) if max_mb is not None else upload_max_mb()
    limit = int(limit_mb * 1024 * 1024)
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > limit:          # Starlette knows the size of a spooled upload
        raise _too_large(limit_mb)
    allowed = _allowed_kinds(allowed_types, allow_heic)
    chunks, total = [], 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _too_large(limit_mb)
        chunks.append(chunk)
        if allowed is not None and len(chunks) == 1 and total >= SNIFF_BYTES and sniff_type(chunk[:SNIFF_BYTES]) not in allowed:
            raise _unsupported(allowed)                         # wrong type: stop reading the rest of a large body
    data = b"".join(chunks)
    if not data:
        if allow_empty:
            return b""
        raise HTTPException(status_code=400, detail="Empty file")
    if allowed is not None and sniff_type(data[:SNIFF_BYTES]) not in allowed:
        raise _unsupported(allowed)
    return data


async def check_upload(file, max_mb: Optional[float] = None, allowed_types: Optional[Iterable[str]] = IMAGES,
                       *, allow_empty: bool = False, allow_heic: bool = False) -> int:
    """Validate like `read_upload`, then rewind the file so the caller can read it again. Returns the size in bytes."""
    data = await read_upload(file, max_mb, allowed_types, allow_empty=allow_empty, allow_heic=allow_heic)
    try:
        await file.seek(0)
    except Exception:                                          # a file object without seek: nothing more to do
        logger.debug("upload could not be rewound")
    return len(data)
